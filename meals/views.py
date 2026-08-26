"""Customer-facing HanyFit Meals views.

Reuses store infrastructure wherever it already does the job:
- store.views.cart_add_meal / store.cart.Cart for adding a meal to the bag
  (checkout for a one-off meal order happens on the existing /store/checkout/
  page, not here -- see store/views.py::checkout, ``requires_delivery``).
- store.views._start_online_payment for the one payment case this app has of
  its own: paying for a Subscription (see _start_subscription_payment below).
"""

from datetime import timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .choices import Goal, MealType, PlanType
from .forms import (
    AddressChangeForm,
    CalculatorForm,
    RescheduleDeliveryForm,
    ReviewForm,
    SubscriptionForm,
)
from .models import Meal, Subscription, SubscriptionPlanPrice
from .services import availability as availability_service
from .services import calculator as calculator_service
from .services import coupons as coupons_service
from .services import notifications as notifications_service
from .services import recommendation as recommendation_service
from .services import reviews as reviews_service
from .services import subscriptions as subscriptions_service

GOAL_CARDS = [
    {"goal": Goal.MUSCLE_GAIN, "emoji": "\U0001F4AA", "title": "Muscle Gain", "subtitle": "Bulking"},
    {"goal": Goal.FAT_LOSS, "emoji": "\U0001F525", "title": "Fat Loss", "subtitle": "Cutting"},
    {"goal": Goal.MAINTAIN, "emoji": "\u2696\uFE0F", "title": "Maintain Weight", "subtitle": "Stay on track"},
]


def home(request):
    featured = Meal.objects.active()[:6]
    plan_prices = SubscriptionPlanPrice.objects.filter(is_active=True).order_by(
        "plan_type", "goal", "meals_per_day"
    )
    return render(
        request,
        "meals/home.html",
        {
            "goal_cards": GOAL_CARDS,
            "featured_meals": featured,
            "weekly_prices": [p for p in plan_prices if p.plan_type == PlanType.WEEKLY],
            "monthly_prices": [p for p in plan_prices if p.plan_type == PlanType.MONTHLY],
            "page_title": "HanyFit Meals",
        },
    )


def meal_list(request):
    goal = request.GET.get("goal", "")
    meal_type = request.GET.get("meal_type", "")
    calorie_max = request.GET.get("calorie_max", "")
    high_protein = request.GET.get("high_protein") == "1"

    meals = (
        Meal.objects.active()
        .for_goal(goal)
        .for_meal_type(meal_type)
    )
    if calorie_max in ("500", "700"):
        meals = meals.under_calories(int(calorie_max))
    if high_protein:
        meals = meals.high_protein()

    today = timezone.localdate()
    meals = list(meals)
    for meal in meals:
        meal.today_availability = availability_service.remaining_for(meal, today)

    return render(
        request,
        "meals/meal_list.html",
        {
            "meals": meals,
            "goal": goal,
            "meal_type": meal_type,
            "calorie_max": calorie_max,
            "high_protein": high_protein,
            "goal_choices": Goal.choices,
            "meal_type_choices": MealType.choices,
            "result_count": len(meals),
            "page_title": "Meal catalogue",
        },
    )


def meal_detail(request, slug):
    meal = get_object_or_404(Meal.objects.active(), slug=slug)
    related_meals = Meal.objects.active().for_goal(meal.goal).exclude(pk=meal.pk)[:4]
    today_availability = availability_service.remaining_for(meal, timezone.localdate())
    return render(
        request,
        "meals/meal_detail.html",
        {
            "meal": meal,
            "related_meals": related_meals,
            "today_availability": today_availability,
            "page_title": meal.name,
        },
    )


def calculator(request):
    result = None
    if request.method == "POST":
        form = CalculatorForm(request.POST)
        if form.is_valid():
            try:
                macros = calculator_service.estimate_macros(
                    form.cleaned_data["age"],
                    form.cleaned_data["height_cm"],
                    form.cleaned_data["weight_kg"],
                    form.cleaned_data["goal"],
                )
            except calculator_service.CalculatorInputError as exc:
                form.add_error(None, str(exc))
            else:
                goal = form.cleaned_data["goal"]
                result = {
                    **macros,
                    "goal": goal,
                    "meals": calculator_service.recommend_meals(
                        goal, macros["calories"], protein_target=macros["protein_g"],
                    ),
                    "plans": calculator_service.recommend_subscription_plans(goal),
                }
    else:
        form = CalculatorForm()

    return render(
        request,
        "meals/calculator.html",
        {"form": form, "result": result, "page_title": "Calorie Calculator"},
    )


def subscribe(request, plan_type):
    if plan_type not in ("weekly", "monthly"):
        messages.error(request, "Choose weekly or monthly.")
        return redirect("meals:home")

    if request.method == "POST":
        form = SubscriptionForm(request.POST, plan_type=plan_type)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                subscription = subscriptions_service.create_subscription(
                    full_name=cd["full_name"],
                    phone=cd["phone"],
                    email=cd["email"],
                    governorate=cd["governorate"],
                    city=cd["city"],
                    address=cd["address"],
                    notes=cd["notes"],
                    plan_type=plan_type,
                    goal=cd["goal"],
                    meals_per_day=cd["meals_per_day"],
                    delivery_weekdays=cd.get("delivery_weekdays"),
                    allergies=cd.get("allergies", ""),
                    disliked_foods=cd.get("disliked_foods", ""),
                    dietary_notes=cd.get("dietary_notes", ""),
                    start_date=cd["start_date"],
                    delivery_slot=cd.get("delivery_slot"),
                    payment_method=cd["payment_method"],
                    coupon_code=cd.get("coupon_code"),
                )
            except subscriptions_service.SubscriptionError as exc:
                form.add_error(None, str(exc))
            except coupons_service.CouponError as exc:
                form.add_error("coupon_code", str(exc))
            else:
                notifications_service.notify(
                    "subscription_created", to_email=subscription.email, name=subscription.full_name,
                    ref=subscription.get_plan_type_display(),
                )
                if subscription.payment_method == Subscription.PaymentMethod.ONLINE:
                    return _start_subscription_payment(request, subscription)
                messages.success(
                    request,
                    "Your subscription request was received. We will contact you "
                    "to confirm before the first delivery.",
                )
                return redirect(subscription.get_absolute_url())
    else:
        form = SubscriptionForm(
            plan_type=plan_type,
            initial={"start_date": timezone.localdate() + timedelta(days=1)},
        )

    return render(
        request,
        "meals/subscribe.html",
        {
            "form": form,
            "plan_type": plan_type,
            "plan_prices": SubscriptionPlanPrice.objects.filter(
                plan_type=plan_type, is_active=True
            ).order_by("goal", "meals_per_day"),
            "page_title": f"{plan_type.title()} Subscription",
        },
    )


def _start_subscription_payment(request, subscription):
    """Create a throwaway store.Order to collect payment for ``subscription``
    through the existing Fawaterk flow, and redirect to the invoice.

    See store/models.py Order.subscription and OrderItem.ItemType.SUBSCRIPTION
    for why this Order exists: Fawaterk's invoice payload requires at least
    one cart item (store/services/fawaterk.py::create_invoice), and this
    keeps the "create invoice, handle webhook, mark paid" code in exactly one
    place instead of a second copy here.
    """
    from store.models import Order, OrderItem
    from store.views import _start_online_payment

    order = Order.objects.create(
        full_name=subscription.full_name,
        phone=subscription.phone,
        email=subscription.email,
        country="EG",
        governorate=subscription.governorate,
        city=subscription.city,
        address=subscription.address,
        notes=f"HanyFit Meals {subscription.get_plan_type_display()} subscription #{subscription.pk}",
        payment_method=Order.PaymentMethod.ONLINE,
        subtotal=subscription.total_price,
        shipping_cost=0,
        discount_amount=0,
        total_price=subscription.total_price,
        currency="EGP",
        session_key=request.session.session_key or "",
        subscription=subscription,
    )
    OrderItem.objects.create(
        order=order,
        item_type=OrderItem.ItemType.SUBSCRIPTION,
        product_name=f"{subscription.get_plan_type_display()} Subscription \u2014 {subscription.get_goal_display()}",
        quantity=1,
        unit_price=subscription.total_price,
    )
    return _start_online_payment(
        request, None, order, retry_redirect=subscription.get_absolute_url()
    )


def _get_subscription_or_404(pk, token):
    """Same pattern as store/views.py::_get_order -- the URL is not
    guessable without the token, so this is the one object-level check every
    self-service action below relies on."""
    return get_object_or_404(Subscription, pk=pk, access_token=token)


def subscription_status(request, pk, token):
    subscription = _get_subscription_or_404(pk, token)
    return render(
        request,
        "meals/subscription_status.html",
        {
            "subscription": subscription,
            "deliveries": subscription.deliveries.all()[:31],
            "recommended_meals": recommendation_service.recommended_for_subscription(subscription),
            "reschedule_form": RescheduleDeliveryForm(),
            "address_form": AddressChangeForm(initial={
                "governorate": subscription.governorate,
                "city": subscription.city,
                "address": subscription.address,
            }),
            "page_title": "Your subscription",
        },
    )


@require_POST
def retry_subscription_payment(request, pk, token):
    subscription = _get_subscription_or_404(pk, token)
    if subscription.is_paid or subscription.payment_method != Subscription.PaymentMethod.ONLINE:
        return redirect(subscription.get_absolute_url())
    return _start_subscription_payment(request, subscription)


@require_POST
def subscription_pause(request, pk, token):
    subscription = _get_subscription_or_404(pk, token)
    try:
        subscriptions_service.pause(subscription)
        messages.success(request, "Your subscription is paused. Deliveries will stop until you resume it.")
        notifications_service.notify("subscription_paused", to_email=subscription.email, name=subscription.full_name)
    except subscriptions_service.SubscriptionError as exc:
        messages.error(request, str(exc))
    return redirect(subscription.get_absolute_url())


@require_POST
def subscription_resume(request, pk, token):
    subscription = _get_subscription_or_404(pk, token)
    try:
        subscriptions_service.resume(subscription)
        messages.success(request, "Your subscription is active again.")
        notifications_service.notify("subscription_resumed", to_email=subscription.email, name=subscription.full_name)
    except subscriptions_service.SubscriptionError as exc:
        messages.error(request, str(exc))
    return redirect(subscription.get_absolute_url())


@require_POST
def subscription_cancel(request, pk, token):
    subscription = _get_subscription_or_404(pk, token)
    try:
        subscriptions_service.cancel(subscription)
        messages.success(request, "Your subscription has been cancelled.")
        notifications_service.notify("subscription_cancelled", to_email=subscription.email, name=subscription.full_name)
    except subscriptions_service.SubscriptionError as exc:
        messages.error(request, str(exc))
    return redirect(subscription.get_absolute_url())


@require_POST
def subscription_renew(request, pk, token):
    subscription = _get_subscription_or_404(pk, token)
    try:
        renewed = subscriptions_service.renew(subscription)
    except subscriptions_service.SubscriptionError as exc:
        messages.error(request, str(exc))
        return redirect(subscription.get_absolute_url())

    notifications_service.notify("subscription_renewed", to_email=renewed.email, name=renewed.full_name)
    if renewed.payment_method == Subscription.PaymentMethod.ONLINE:
        return _start_subscription_payment(request, renewed)
    messages.success(request, "Your subscription has been renewed.")
    return redirect(renewed.get_absolute_url())


@require_POST
def subscription_skip_delivery(request, pk, token, delivery_id):
    subscription = _get_subscription_or_404(pk, token)
    delivery = get_object_or_404(subscription.deliveries, pk=delivery_id)
    try:
        subscriptions_service.skip_delivery(delivery)
        messages.success(request, f"Delivery on {delivery.scheduled_date} has been skipped.")
    except subscriptions_service.SubscriptionError as exc:
        messages.error(request, str(exc))
    return redirect(subscription.get_absolute_url())


@require_POST
def subscription_reschedule_delivery(request, pk, token, delivery_id):
    subscription = _get_subscription_or_404(pk, token)
    delivery = get_object_or_404(subscription.deliveries, pk=delivery_id)
    form = RescheduleDeliveryForm(request.POST)
    if form.is_valid():
        try:
            subscriptions_service.reschedule_delivery(delivery, form.cleaned_data["new_date"])
            messages.success(request, "Delivery rescheduled.")
        except subscriptions_service.SubscriptionError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Choose a valid date.")
    return redirect(subscription.get_absolute_url())


@require_POST
def subscription_change_address(request, pk, token):
    subscription = _get_subscription_or_404(pk, token)
    form = AddressChangeForm(request.POST)
    if form.is_valid():
        try:
            subscriptions_service.change_address(subscription, **form.cleaned_data)
            messages.success(request, "Delivery address updated.")
        except subscriptions_service.SubscriptionError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Please fix the address details below.")
    return redirect(subscription.get_absolute_url())


@require_POST
def add_review(request, order_number, token, item_id):
    """Reviews are submitted from the existing guest order-tracking page
    (store/views.py::order_detail, /store/orders/<number>/<token>/) -- the
    same access_token pattern proves this is genuinely the customer's order,
    with no new auth mechanism."""
    from store.models import Order

    order = get_object_or_404(Order, order_number=order_number, access_token=token)
    order_item = get_object_or_404(order.items, pk=item_id)
    form = ReviewForm(request.POST)

    redirect_url = reverse("order_detail", kwargs={"order_number": order_number, "token": token})

    if not form.is_valid():
        messages.error(request, "Please choose a rating from 1 to 5.")
        return redirect(redirect_url)

    try:
        reviews_service.create_review(
            order_item, rating=form.cleaned_data["rating"], comment=form.cleaned_data["comment"],
        )
        messages.success(request, "Thanks for the review!")
    except reviews_service.ReviewError as exc:
        messages.error(request, str(exc))

    return redirect(redirect_url)
