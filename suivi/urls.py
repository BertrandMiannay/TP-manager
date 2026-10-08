from django.urls import path

from . import views

urlpatterns = [
    path("", views.session_list, name="suivi-sessions"),
    path("seances/creer/", views.session_create, name="suivi-session-create"),
    path("seances/<int:pk>/", views.session_detail, name="suivi-session-detail"),
    path("seances/<int:pk>/supprimer/", views.session_delete, name="suivi-session-delete"),
    path("presences/", views.attendance_overview, name="suivi-attendance"),
    path("eleves/", views.student_list, name="suivi-students"),
    path("eleves/<int:pk>/", views.student_detail, name="suivi-student-detail"),
    path("mon-suivi/", views.my_tracking, name="suivi-mine"),
]
