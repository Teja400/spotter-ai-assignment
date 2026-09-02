from django.urls import path

from fueloptimizer import views

app_name = "fueloptimizer"

urlpatterns = [
    path("plan/", views.plan_route, name="plan_route"),
    path("stations/register/", views.register_station, name="register_station"),
]
