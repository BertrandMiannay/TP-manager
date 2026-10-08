from django.contrib import admin

from .models import Attendance, Exercise, StudentNote, TrainingSession


class AttendanceInline(admin.TabularInline):
    model = Attendance
    extra = 0
    autocomplete_fields = ['member']


@admin.register(TrainingSession)
class TrainingSessionAdmin(admin.ModelAdmin):
    list_display      = ('date', 'title', 'season')
    list_filter       = ('season',)
    filter_horizontal = ('cursus', 'skills', 'instructors')
    inlines           = [AttendanceInline]


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('session', 'member', 'status')
    list_filter  = ('status', 'session__season')


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ('date', 'member', 'name', 'result')
    list_filter  = ('result',)


@admin.register(StudentNote)
class StudentNoteAdmin(admin.ModelAdmin):
    list_display = ('date', 'member', 'visibility', 'author')
    list_filter  = ('visibility',)
