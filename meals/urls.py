from django.urls import path

from . import views

app_name = "meals"

urlpatterns = [
    path("", views.home, name="home"),
    path("catalogue/", views.meal_list, name="meal_list"),
    path("calculator/", views.calculator, name="calculator"),
    path("subscribe/<str:plan_type>/", views.subscribe, name="subscribe"),
    path(
        "subscription/<int:pk>/<str:token>/",
        views.subscription_status,
        name="subscription_status",
    ),
    path(
        "subscription/<int:pk>/<str:token>/retry-payment/",
        views.retry_subscription_payment,
        name="retry_subscription_payment",
    ),
    path("subscription/<int:pk>/<str:token>/pause/", views.subscription_pause, name="subscription_pause"),
    path("subscription/<int:pk>/<str:token>/resume/", views.subscription_resume, name="subscription_resume"),
    path("subscription/<int:pk>/<str:token>/cancel/", views.subscription_cancel, name="subscription_cancel"),
    path("subscription/<int:pk>/<str:token>/renew/", views.subscription_renew, name="subscription_renew"),
    path(
        "subscription/<int:pk>/<str:token>/address/",
        views.subscription_change_address,
        name="subscription_change_address",
    ),
    path(
        "subscription/<int:pk>/<str:token>/deliveries/<int:delivery_id>/skip/",
        views.subscription_skip_delivery,
        name="subscription_skip_delivery",
    ),
    path(
        "subscription/<int:pk>/<str:token>/deliveries/<int:delivery_id>/reschedule/",
        views.subscription_reschedule_delivery,
        name="subscription_reschedule_delivery",
    ),
    path(
        "reviews/<str:order_number>/<str:token>/<int:item_id>/add/",
        views.add_review,
        name="add_review",
    ),
    # Kept last: a bare "<slug>/" would otherwise swallow every path above.
    path("<slug:slug>/", views.meal_detail, name="meal_detail"),
]
