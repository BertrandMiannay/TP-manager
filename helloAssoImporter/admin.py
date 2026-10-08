import logging
from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html

from .models import Season, MemberShipForm, MemberShipFormOrder, Member, EventForm, EventFormOrder, EventRegistration, Cursus, CursusCategory, Skill, MemberSkill, SkillEvaluation

logger = logging.getLogger(__name__)


class DeleteAuditAdminMixin:
    """Annotates list with related count and logs a warning before any deletion."""

    def _related_count(self, obj):
        raise NotImplementedError

    def _batch_counts(self, queryset):
        raise NotImplementedError

    @staticmethod
    def _format_count(count):
        if count > 0:
            return format_html('<strong style="color:#c0392b">{} inscription(s)</strong>', count)
        return '0'

    def delete_model(self, request, obj):
        count = self._related_count(obj)
        logger.warning("ADMIN_DELETE %s pk=%s title=%s count=%d by=%s",
                       type(obj).__name__, obj.pk, obj.title, count, request.user.username)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        counts = self._batch_counts(queryset)
        for obj in queryset:
            logger.warning("ADMIN_DELETE %s pk=%s title=%s count=%d by=%s",
                           type(obj).__name__, obj.pk, obj.title, counts.get(obj.pk, 0), request.user.username)
        super().delete_queryset(request, queryset)


@admin.register(EventForm)
class EventFormAdmin(DeleteAuditAdminMixin, admin.ModelAdmin):
    list_display = ('title', 'start_date', 'end_date', 'registration_count')
    ordering = ('-start_date',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _registration_count=Count('eventformorder__eventregistration', distinct=True)
        )

    def registration_count(self, obj):
        return self._format_count(obj._registration_count)
    registration_count.short_description = 'Inscriptions (supprimées en cascade)'
    registration_count.admin_order_field = '_registration_count'

    def _related_count(self, obj):
        return EventRegistration.objects.filter(order__form=obj).count()

    def _batch_counts(self, queryset):
        return {
            row['order__form_id']: row['n']
            for row in EventRegistration.objects
            .filter(order__form__in=queryset)
            .values('order__form_id')
            .annotate(n=Count('id'))
        }


@admin.register(MemberShipForm)
class MemberShipFormAdmin(DeleteAuditAdminMixin, admin.ModelAdmin):
    list_display = ('title', 'season', 'order_count')

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _order_count=Count('membershipformorder', distinct=True)
        )

    def order_count(self, obj):
        return self._format_count(obj._order_count)
    order_count.short_description = 'Inscriptions (supprimées en cascade)'
    order_count.admin_order_field = '_order_count'

    def _related_count(self, obj):
        return MemberShipFormOrder.objects.filter(form=obj).count()

    def _batch_counts(self, queryset):
        return {
            row['form_id']: row['n']
            for row in MemberShipFormOrder.objects
            .filter(form__in=queryset)
            .values('form_id')
            .annotate(n=Count('id'))
        }


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("first_name", "last_name", "email", "medical_certificate_date")
    search_fields = ("first_name", "last_name", "email")
    fields = ("first_name", "last_name", "email", "medical_certificate_date")


admin.site.register(Season)
admin.site.register(MemberShipFormOrder)
admin.site.register(EventFormOrder)
admin.site.register(EventRegistration)


class SkillInline(admin.TabularInline):
    model = Skill
    extra = 0
    ordering = ['order']


class CursusCategoryInline(admin.TabularInline):
    model = CursusCategory
    extra = 0
    ordering = ['order']


@admin.register(Cursus)
class CursusAdmin(admin.ModelAdmin):
    list_display = ('name', 'date', 'status')
    list_filter  = ('status',)
    inlines      = [CursusCategoryInline]


@admin.register(CursusCategory)
class CursusCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'cursus', 'order')
    inlines      = [SkillInline]


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'order')


admin.site.register(MemberSkill)
@admin.register(SkillEvaluation)
class SkillEvaluationAdmin(admin.ModelAdmin):
    list_display = ('date', 'member', 'skill', 'status', 'author')
    list_filter  = ('status', 'author')
