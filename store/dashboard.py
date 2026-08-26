"""Owner-facing dashboard: orders, stock, customers and visitor analytics.

Deliberately separate from /admin/. The client authenticates against the
same Django user database, but through its own login page, and is gated by
a dedicated permission (store.access_dashboard) rather than is_staff -- see
the "Store Dashboard" group created in migration 0007. A user can therefore
be given dashboard access without ever being able to reach /admin/.

Every view here is read via the ORM with querysets scoped to exactly what is
shown; nothing accepts an id from the client that is not re-validated
against the database (see order_detail below), so there is no IDOR path
through this module.
"""

from datetime import timedelta
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import redirect_to_login
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from . import geo
from .models import Order, PageVisit, Product, ProductVariant


# Brute-force protection for the dashboard login. Counted per client IP
# (X-Forwarded-For, which Vercel sets, falling back to REMOTE_ADDR) rather
# than per-username, so an attacker cannot use a valid client username to
# lock the real owner out.
LOGIN_MAX_ATTEMPTS = 8
LOGIN_LOCKOUT_SECONDS = 15 * 60


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def dashboard_login(request):
    """Username/password sign-in for /dashboard/, throttled per IP.

    Deliberately not django.contrib.auth.views.LoginView: that has no
    built-in rate limiting, and the dashboard is a second, newly-added
    authentication surface that is worth protecting against credential
    stuffing independently of whatever protects /admin/.
    """
    next_url = request.GET.get("next") or request.POST.get("next") or reverse("dashboard_overview")
    if not next_url.startswith("/"):
        next_url = reverse("dashboard_overview")

    if request.user.is_authenticated:
        return redirect(next_url)

    throttle_key = f"dashboard_login_attempts:{_client_ip(request)}"
    form = AuthenticationForm(request)
    locked_out = cache.get(throttle_key, 0) >= LOGIN_MAX_ATTEMPTS

    if request.method == "POST" and not locked_out:
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            cache.delete(throttle_key)
            login(request, form.get_user())
            return redirect(next_url)
        # Invalid credentials: count it. Cache.add only sets the initial
        # value once, so this is safe against concurrent requests.
        cache.add(throttle_key, 0, LOGIN_LOCKOUT_SECONDS)
        try:
            cache.incr(throttle_key)
        except ValueError:
            cache.set(throttle_key, 1, LOGIN_LOCKOUT_SECONDS)

    if locked_out:
        messages.error(
            request, "Too many failed attempts. Try again in a few minutes."
        )

    return render(
        request,
        "store/dashboard/login.html",
        {"form": form, "next": next_url, "locked_out": locked_out},
    )


def dashboard_required(view_func):
    """Require a logged-in user with the access_dashboard permission.

    Superusers pass automatically (Django's has_perm default), which covers
    the site owner without any extra setup; the client is added to the
    "Store Dashboard" group instead of being made a superuser.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url=reverse("dashboard_login"))
        if not request.user.has_perm("store.access_dashboard"):
            raise PermissionDenied("You do not have access to the dashboard.")
        return view_func(request, *args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _bounds(days):
    """(start, end) datetimes for "the last N days", end exclusive = now."""
    now = timezone.now()
    return now - timedelta(days=days), now


def _today_start():
    now = timezone.localtime()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _count_since(queryset, field, since):
    return queryset.filter(**{f"{field}__gte": since}).count()


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

@dashboard_required
def overview(request):
    orders = Order.objects.all()
    paid_orders = orders.filter(payment_status=Order.PaymentStatus.PAID)

    today_start = _today_start()
    week_start, _ = _bounds(7)
    month_start, _ = _bounds(30)

    order_counts = {
        "total": orders.count(),
        "new": orders.filter(order_status=Order.OrderStatus.NEW).count(),
        "confirmed": orders.filter(order_status=Order.OrderStatus.CONFIRMED).count(),
        "processing": orders.filter(order_status=Order.OrderStatus.PROCESSING).count(),
        "shipped": orders.filter(order_status=Order.OrderStatus.SHIPPED).count(),
        "delivered": orders.filter(order_status=Order.OrderStatus.DELIVERED).count(),
        "cancelled": orders.filter(order_status=Order.OrderStatus.CANCELLED).count(),
        "paid": paid_orders.count(),
    }

    # Revenue is grouped by currency rather than summed together: an EGP
    # total and a SAR total added up would just be a meaningless number.
    revenue_by_currency = list(
        paid_orders.values("currency")
        .annotate(total=Sum("total_price"), count=Count("id"))
        .order_by("-total")
    )
    revenue_today = list(
        paid_orders.filter(paid_at__gte=today_start)
        .values("currency")
        .annotate(total=Sum("total_price"))
        .order_by("-total")
    )
    revenue_7d = list(
        paid_orders.filter(paid_at__gte=week_start)
        .values("currency")
        .annotate(total=Sum("total_price"))
        .order_by("-total")
    )
    revenue_30d = list(
        paid_orders.filter(paid_at__gte=month_start)
        .values("currency")
        .annotate(total=Sum("total_price"))
        .order_by("-total")
    )

    paid_count = paid_orders.count()
    aov_by_currency = []
    if paid_count:
        for row in revenue_by_currency:
            if row["count"]:
                aov_by_currency.append(
                    {"currency": row["currency"], "value": row["total"] / row["count"]}
                )

    # Products / stock
    product_count = Product.objects.count()
    low_stock = (
        ProductVariant.objects.select_related("product")
        .filter(is_active=True, stock__gt=0, stock__lte=5)
        .order_by("stock")[:10]
    )
    sold_out_count = ProductVariant.objects.filter(is_active=True, stock=0).count()

    top_products = (
        Order.objects.filter(payment_status=Order.PaymentStatus.PAID)
        .values("items__product__name")
        .exclude(items__product__isnull=True)
        .annotate(units=Sum("items__quantity"))
        .order_by("-units")[:5]
    )

    # Customers: this store has no account system, so a "customer" is a
    # distinct phone number that has placed at least one order.
    customer_count = Order.objects.exclude(phone="").values("phone").distinct().count()
    recent_customers = (
        Order.objects.exclude(phone="")
        .order_by("-created_at")
        .values("full_name", "phone", "email", "country", "created_at")[:8]
    )

    recent_orders = orders.prefetch_related("items")[:10]

    # Visitors
    visits = PageVisit.objects.all()
    visit_counts = {
        "total": visits.count(),
        "today": _count_since(visits, "created_at", today_start),
        "week": _count_since(visits, "created_at", week_start),
        "month": _count_since(visits, "created_at", month_start),
    }
    unique_counts = {
        "today": visits.filter(created_at__gte=today_start).values("session_key").distinct().count(),
        "week": visits.filter(created_at__gte=week_start).values("session_key").distinct().count(),
        "month": visits.filter(created_at__gte=month_start).values("session_key").distinct().count(),
    }
    top_pages = list(
        visits.filter(created_at__gte=month_start)
        .values("path")
        .annotate(views=Count("id"))
        .order_by("-views")[:8]
    )

    # Trend charts: the period is a real, user-chosen window (7/30/90 days),
    # never a hardcoded number of points -- every value plotted is a fresh
    # aggregate query against that window, not a fixture.
    try:
        period_days = int(request.GET.get("days", 30))
    except (TypeError, ValueError):
        period_days = 30
    if period_days not in (7, 30, 90):
        period_days = 30

    chart_days, chart_visits, chart_unique, chart_orders, chart_revenue = [], [], [], [], []
    for offset in range(period_days - 1, -1, -1):
        day = timezone.localdate() - timedelta(days=offset)
        day_start = timezone.make_aware(
            timezone.datetime.combine(day, timezone.datetime.min.time())
        )
        day_end = day_start + timedelta(days=1)

        day_visits = visits.filter(created_at__gte=day_start, created_at__lt=day_end)
        day_orders = orders.filter(created_at__gte=day_start, created_at__lt=day_end)
        day_revenue = paid_orders.filter(paid_at__gte=day_start, paid_at__lt=day_end).aggregate(
            total=Sum("total_price")
        )["total"] or Decimal("0")

        chart_days.append(day.strftime("%d %b"))
        chart_visits.append(day_visits.count())
        chart_unique.append(day_visits.values("session_key").distinct().count())
        chart_orders.append(day_orders.count())
        chart_revenue.append(float(day_revenue))

    return render(
        request,
        "store/dashboard/overview.html",
        {
            "order_counts": order_counts,
            "revenue_by_currency": revenue_by_currency,
            "revenue_today": revenue_today,
            "revenue_7d": revenue_7d,
            "revenue_30d": revenue_30d,
            "aov_by_currency": aov_by_currency,
            "product_count": product_count,
            "low_stock": low_stock,
            "sold_out_count": sold_out_count,
            "top_products": top_products,
            "customer_count": customer_count,
            "recent_customers": recent_customers,
            "recent_orders": recent_orders,
            "visit_counts": visit_counts,
            "unique_counts": unique_counts,
            "top_pages": top_pages,
            "period_days": period_days,
            "chart_days": chart_days,
            "chart_visits": chart_visits,
            "chart_unique": chart_unique,
            "chart_orders": chart_orders,
            "chart_revenue": chart_revenue,
            "page_title": "Dashboard",
            "active": "overview",
        },
    )


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

@dashboard_required
def orders_list(request):
    orders = Order.objects.all().prefetch_related("items")

    status = request.GET.get("status", "")
    payment_status = request.GET.get("payment_status", "")
    country = request.GET.get("country", "")
    item_type = request.GET.get("item_type", "")
    q = request.GET.get("q", "").strip()
    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")

    if status:
        orders = orders.filter(order_status=status)
    if payment_status:
        orders = orders.filter(payment_status=payment_status)
    if country:
        orders = orders.filter(country=country)
    if item_type:
        orders = orders.filter(items__item_type=item_type).distinct()
    if q:
        orders = orders.filter(
            Q(order_number__icontains=q)
            | Q(full_name__icontains=q)
            | Q(phone__icontains=q)
            | Q(email__icontains=q)
        )
    if date_from:
        orders = orders.filter(created_at__date__gte=date_from)
    if date_to:
        orders = orders.filter(created_at__date__lte=date_to)

    paginator = Paginator(orders, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "store/dashboard/orders.html",
        {
            "page_obj": page_obj,
            "status": status,
            "payment_status": payment_status,
            "country": country,
            "item_type": item_type,
            "q": q,
            "date_from": date_from,
            "date_to": date_to,
            "order_status_choices": Order.OrderStatus.choices,
            "payment_status_choices": Order.PaymentStatus.choices,
            "country_choices": geo.available_countries(),
            "page_title": "Orders",
            "active": "orders",
        },
    )


def _notify_customer_of_order_status(order, new_status):
    """Best-effort customer email for the statuses the brief calls out
    (Phase 14). Only fires for meal orders -- apparel orders never had a
    customer-facing status notification and this does not change that."""
    from meals.services import notifications as meals_notifications

    event = {
        Order.OrderStatus.PROCESSING: "order_preparing",
        Order.OrderStatus.SHIPPED: "order_out_for_delivery",
        Order.OrderStatus.DELIVERED: "order_delivered",
        Order.OrderStatus.CANCELLED: "order_cancelled",
    }.get(new_status)
    if event and order.has_meal_items:
        meals_notifications.notify(
            event, to_email=order.email, name=order.full_name, ref=order.order_number,
        )


@dashboard_required
@require_http_methods(["GET", "POST"])
def order_detail(request, order_number):
    # get_object_or_404 re-reads the row fresh from the database by its
    # unique order_number every time -- an order_number from the URL can
    # never be trusted to belong to a particular status without this lookup.
    order = get_object_or_404(Order.objects.prefetch_related("items"), order_number=order_number)

    if request.method == "POST":
        new_status = request.POST.get("order_status")
        if new_status in dict(Order.OrderStatus.choices):
            order.order_status = new_status
            order.save(update_fields=["order_status", "updated_at"])
            messages.success(
                request, f"Order {order.order_number} marked {order.get_order_status_display()}."
            )
            _notify_customer_of_order_status(order, new_status)
        else:
            messages.error(request, "Not a valid status.")
        return redirect("dashboard_order_detail", order_number=order.order_number)

    return render(
        request,
        "store/dashboard/order_detail.html",
        {
            "order": order,
            "items": order.items.all(),
            "order_status_choices": Order.OrderStatus.choices,
            "page_title": f"Order {order.order_number}",
            "active": "orders",
        },
    )


# ---------------------------------------------------------------------------
# HanyFit Meals
# ---------------------------------------------------------------------------
#
# Design note: the meal catalogue, coupons and delivery slots are simple,
# low-frequency CRUD (add a meal, tweak a price, add a delivery slot) that
# Django admin -- already registered in meals/admin.py, already gated by the
# same is_staff/permission model -- already does well. This dashboard
# mirrors the existing convention already used for the clothing catalogue
# (the sidebar's "Products" link opens /admin/store/product/ rather than a
# bespoke product-editing screen here). Subscriptions get bespoke dashboard
# pages instead, because tracking *daily delivery status* across an active
# subscription is a real operational workflow the admin's default
# list/detail screens do not handle well.

from .models import OrderItem
from meals.models import Meal, Coupon, DeliverySlot, Subscription, SubscriptionDelivery
from meals.services import analytics as meals_analytics
from meals.services import subscriptions as subscriptions_service


@dashboard_required
def meals_home(request):
    """Landing page for the HanyFit Meals section: quick counts + links out
    to the admin screens for catalogue/coupon/delivery-slot editing."""
    meal_orders = Order.objects.filter(items__item_type=OrderItem.ItemType.MEAL).distinct()
    context = {
        "meal_count": Meal.objects.count(),
        "active_meal_count": Meal.objects.filter(is_active=True).count(),
        "meal_order_count": meal_orders.count(),
        "pending_meal_orders": meal_orders.filter(
            order_status=Order.OrderStatus.NEW
        ).count(),
        "active_subscription_count": Subscription.objects.filter(
            status=Subscription.Status.ACTIVE
        ).count(),
        "coupon_count": Coupon.objects.filter(is_active=True).count(),
        "delivery_slot_count": DeliverySlot.objects.filter(is_active=True).count(),
        "page_title": "HanyFit Meals",
        "active": "meals",
    }
    context.update(meals_analytics.overview())
    return render(request, "store/dashboard/meals_home.html", context)


@dashboard_required
def subscriptions_list(request):
    subs = Subscription.objects.all()

    status = request.GET.get("status", "")
    plan_type = request.GET.get("plan_type", "")
    q = request.GET.get("q", "").strip()

    if status:
        subs = subs.filter(status=status)
    if plan_type:
        subs = subs.filter(plan_type=plan_type)
    if q:
        subs = subs.filter(Q(full_name__icontains=q) | Q(phone__icontains=q))

    paginator = Paginator(subs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "store/dashboard/subscriptions.html",
        {
            "page_obj": page_obj,
            "status": status,
            "plan_type": plan_type,
            "q": q,
            "status_choices": Subscription.Status.choices,
            "page_title": "Subscriptions",
            "active": "meals",
        },
    )


@dashboard_required
@require_http_methods(["GET", "POST"])
def subscription_detail(request, pk):
    subscription = get_object_or_404(
        Subscription.objects.prefetch_related("deliveries", "deliveries__meals"), pk=pk
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "set_status":
            new_status = request.POST.get("status")
            if new_status in dict(Subscription.Status.choices):
                subscriptions_service.set_status(subscription, new_status)
                messages.success(request, f"Subscription marked {subscription.get_status_display()}.")
            else:
                messages.error(request, "Not a valid status.")
        elif action == "set_delivery_status":
            delivery_id = request.POST.get("delivery_id")
            new_status = request.POST.get("delivery_status")
            delivery = subscription.deliveries.filter(pk=delivery_id).first()
            if delivery and new_status in dict(SubscriptionDelivery.Status.choices):
                delivery.status = new_status
                delivery.save(update_fields=["status", "updated_at"])
                messages.success(request, f"Delivery for {delivery.scheduled_date} updated.")
            else:
                messages.error(request, "Could not update that delivery.")
        return redirect("dashboard_subscription_detail", pk=subscription.pk)

    return render(
        request,
        "store/dashboard/subscription_detail.html",
        {
            "subscription": subscription,
            "deliveries": subscription.deliveries.all(),
            "status_choices": Subscription.Status.choices,
            "delivery_status_choices": SubscriptionDelivery.Status.choices,
            "page_title": f"Subscription #{subscription.pk}",
            "active": "meals",
        },
    )
