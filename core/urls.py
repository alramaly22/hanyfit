from django.urls import path

from . import views

urlpatterns = [
    path("set-currency/", views.set_currency, name="set_currency"),
]
