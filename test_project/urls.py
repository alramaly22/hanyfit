from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts import views
from accounts import seo as accounts_seo
from store import dashboard as store_dashboard
from store import views as store_views

urlpatterns = [
    path("", views.index, name="index"),
    path("robots.txt", accounts_seo.robots_txt, name="robots_txt"),
    path("sitemap.xml", accounts_seo.sitemap_xml, name="sitemap_xml"),
    path("about/", views.about, name="about"),
    path("pricing/", views.pricing, name="pricing"),
    path("second/", views.second, name="second"),
    path("book/", views.book, name="book"),

    # Arabic calculators
    path("protein/", views.protein, name="protein"),
    path("calories/", views.calories, name="calories"),

    # English calculators
    path("proteinen/", views.proteinen, name="proteinen"),
    path("caloriesen/", views.caloriesen, name="caloriesen"),

    path("store/", include("store.urls")),
    path("meals/", include("meals.urls")),

    # Kept so the webhook URL already registered in the Fawaterk dashboard
    # keeps working. New deployments should point at store/payment/webhook_json/
    # instead, which is the URL that receives a JSON body.
    path("webhook/paid/", store_views.fawaterk_webhook, name="paid_webhook_legacy"),

    # ------------------------------------------------------------------
    # Client dashboard. Entirely separate from /admin/: its own login page,
    # its own permission (store.access_dashboard, see store/dashboard.py),
    # and none of the model-editing/bulk-delete power of the Django admin.
    # ------------------------------------------------------------------
    path("dashboard/login/", store_dashboard.dashboard_login, name="dashboard_login"),
    path(
        "dashboard/logout/",
        auth_views.LogoutView.as_view(next_page="dashboard_login"),
        name="dashboard_logout",
    ),
    path("dashboard/", store_dashboard.overview, name="dashboard_overview"),
    path("dashboard/orders/", store_dashboard.orders_list, name="dashboard_orders"),
    path(
        "dashboard/orders/<str:order_number>/",
        store_dashboard.order_detail,
        name="dashboard_order_detail",
    ),

    # HanyFit Meals section of the dashboard. Meal catalogue / coupon /
    # delivery-slot CRUD stays on Django admin (see store/dashboard.py
    # meals_home docstring); subscriptions get bespoke pages here.
    path("dashboard/meals/", store_dashboard.meals_home, name="dashboard_meals_home"),
    path(
        "dashboard/meals/subscriptions/",
        store_dashboard.subscriptions_list,
        name="dashboard_subscriptions",
    ),
    path(
        "dashboard/meals/subscriptions/<int:pk>/",
        store_dashboard.subscription_detail,
        name="dashboard_subscription_detail",
    ),

    path("admin/", admin.site.urls),
]

# Django only serves media through this helper in development; on Vercel the
# filesystem is read-only and assets are served from static/ or a CDN.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
