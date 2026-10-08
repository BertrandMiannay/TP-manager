import json
import logging
import time
from urllib.parse import quote
from itertools import groupby as itertools_groupby
from django import forms

logger = logging.getLogger(__name__)
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from datetime import timedelta

from common.api.helloAssoApi import get_hello_asso_api, HelloAssoApiError, FIELD_EMAIL, FIELD_BIRTHDATE, FIELD_SEX, FIELD_LICENCE, LEVEL_FIELD_LABELS, CONTACT_FIELD_LABELS
from helloAssoImporter.models import Season, MemberShipForm, MemberShipFormOrder, Member, EventForm, EventRegistration, Cursus, CursusCategory, Skill, MemberSkill, SkillEvaluation
from django.views.generic import ListView, DetailView
from django.db.models import Count, Max, Q
from django.core.cache import cache
from userManagement.views import AdminRequiredMixin, admin_required, ClubStaffRequiredMixin, club_staff_required


class EventFormCreateForm(forms.Form):
    title = forms.CharField(label='Titre', max_length=200)
    description = forms.CharField(label='Description courte', required=False, widget=forms.Textarea(attrs={'rows': 3}))
    long_description = forms.CharField(label='Description longue', required=False, widget=forms.Textarea(attrs={'rows': 5}))
    start_date = forms.DateTimeField(label='Date de début', required=False, widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    end_date = forms.DateTimeField(label='Date de fin', required=False, widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    place = forms.CharField(label='Lieu', required=False)
    max_entries = forms.IntegerField(label='Nombre max de participants', required=False, min_value=1)


class MemberShipFormListView(AdminRequiredMixin, ListView):
    model = MemberShipForm
    template_name = 'helloAssoImporter/membership_forms.html'
    context_object_name = 'membership_forms'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['seasons'] = Season.objects.all().order_by('-current', 'label')
        ctx['active_tab'] = 'formulaires'
        return ctx


class MemberShipFormOrderListView(LoginRequiredMixin, ListView):
    model = MemberShipFormOrder
    template_name = 'forms.html'


class EventFormListView(ClubStaffRequiredMixin, ListView):
    model = EventForm
    template_name = 'helloAssoImporter/event_forms.html'
    context_object_name = 'event_forms'

    def get_queryset(self):
        return EventForm.objects.annotate(
            registration_count=Count('eventformorder__eventregistration'),
            confirmed_count=Count(
                'eventformorder__eventregistration',
                filter=Q(eventformorder__eventregistration__state=EventRegistration.State.REGISTERED)
            ),
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_tab'] = 'sorties'
        return ctx


class EventFormDetailView(ClubStaffRequiredMixin, DetailView):
    model = EventForm
    template_name = 'helloAssoImporter/event_form_detail.html'
    context_object_name = 'event_form'
    pk_url_kwarg = 'form_slug'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['registrations'] = EventRegistration.objects.filter(
            order__form=self.object
        ).select_related('order').order_by('state', 'last_name', 'first_name')
        return ctx


def notify_api_error(request, error: HelloAssoApiError):
    logger.error("HelloAsso API error: %s", error)
    messages.error(request, "Échec de la communication avec HelloAsso. Veuillez réessayer.")


@admin_required
def season_gestion(request):
    form_data = {}
    if request.method == 'POST':
        label = request.POST.get('label', '').strip()
        if label:
            Season.objects.create(label=label)
            return redirect('saison-gestion')
        form_data = {'label': label}
    seasons = Season.objects.all().order_by('-current', 'label')
    return render(request, 'helloAssoImporter/season_gestion.html', {
        'seasons': seasons,
        'form_data': form_data,
        'active_tab': 'gestion',
    })


@admin_required
def set_current_season(request, pk):
    if request.method == 'POST':
        Season.objects.update(current=False)
        Season.objects.filter(pk=pk).update(current=True)
    return redirect('saison-gestion')


@admin_required
def delete_season(request, pk):
    if request.method == 'POST':
        season = Season.objects.filter(pk=pk).first()
        if season:
            logger.info("SEASON_DELETE pk=%s label=%s by=%s", pk, season.label, request.user.username)
            season.delete()
    return redirect('saison-gestion')


@admin_required
def formation_list(request):
    cursus_list = Cursus.objects.annotate(
        category_count=Count('categories'),
    ).order_by('status', '-date')
    return render(request, 'helloAssoImporter/formation.html', {
        'cursus_list': cursus_list,
        'active_tab': 'formation',
    })


@admin_required
def cursus_create(request):
    error = None
    form_data = {}
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        date_str = request.POST.get('date', '').strip()
        form_data = {'name': name, 'date': date_str}
        if not name:
            error = "Le nom du cursus est obligatoire."
        elif not date_str:
            error = "La date de version est obligatoire."
        else:
            from datetime import date as date_cls
            try:
                parsed_date = date_cls.fromisoformat(date_str)
            except ValueError:
                error = "Format de date invalide (attendu : AAAA-MM-JJ)."
            else:
                cursus = Cursus.objects.create(name=name, date=parsed_date)
                logger.info("CURSUS_CREATE pk=%s name=%s by=%s", cursus.pk, cursus.name, request.user.username)
                messages.success(request, f"Cursus « {cursus.name} » créé.")
                return redirect('saison-cursus-detail', pk=cursus.pk)
    return render(request, 'helloAssoImporter/cursus_create.html', {
        'error': error,
        'form_data': form_data,
        'active_tab': 'formation',
    })


@admin_required
def cursus_detail(request, pk):
    cursus = get_object_or_404(Cursus, pk=pk)

    if request.method == 'POST':
        action = request.POST.get('_action')

        if action == 'edit_cursus':
            name = request.POST.get('name', '').strip()
            date_str = request.POST.get('date', '').strip()
            if name and date_str:
                from datetime import date as date_cls
                try:
                    cursus.name = name
                    cursus.date = date_cls.fromisoformat(date_str)
                    cursus.save(update_fields=['name', 'date'])
                    messages.success(request, "Cursus mis à jour.")
                except ValueError:
                    messages.error(request, "Format de date invalide.")
            return redirect('saison-cursus-detail', pk=pk)

        elif action == 'add_category':
            name = request.POST.get('category_name', '').strip()
            if name:
                max_order = cursus.categories.aggregate(m=Max('order'))['m'] or 0
                CursusCategory.objects.create(cursus=cursus, name=name, order=max_order + 1)
            return redirect('saison-cursus-detail', pk=pk)

        elif action == 'delete_category':
            cat_pk = request.POST.get('category_pk')
            CursusCategory.objects.filter(pk=cat_pk, cursus=cursus).delete()
            return redirect('saison-cursus-detail', pk=pk)

        elif action == 'reorder_categories':
            order_list = request.POST.getlist('category_order')
            with transaction.atomic():
                for i, cat_pk in enumerate(order_list, start=1):
                    CursusCategory.objects.filter(pk=cat_pk, cursus=cursus).update(order=i)
            return redirect('saison-cursus-detail', pk=pk)

        elif action == 'add_skill':
            cat_pk = request.POST.get('category_pk')
            name = request.POST.get('skill_name', '').strip()
            if name and cat_pk:
                category = get_object_or_404(CursusCategory, pk=cat_pk, cursus=cursus)
                max_order = category.skills.aggregate(m=Max('order'))['m'] or 0
                Skill.objects.create(category=category, name=name, order=max_order + 1)
            return redirect('saison-cursus-detail', pk=pk)

        elif action == 'delete_skill':
            skill_pk = request.POST.get('skill_pk')
            Skill.objects.filter(pk=skill_pk, category__cursus=cursus).delete()
            return redirect('saison-cursus-detail', pk=pk)

        elif action == 'reorder_skills':
            cat_pk = request.POST.get('category_pk')
            order_list = request.POST.getlist('skill_order')
            with transaction.atomic():
                for i, skill_pk in enumerate(order_list, start=1):
                    Skill.objects.filter(pk=skill_pk, category_id=cat_pk).update(order=i)
            return redirect('saison-cursus-detail', pk=pk)

    categories = cursus.categories.prefetch_related('skills').all()
    cursus_json = json.dumps({
        'name': cursus.name,
        'date': cursus.date.isoformat(),
        'categories': [
            {
                'name': cat.name,
                'skills': [{'name': skill.name} for skill in cat.skills.all()],
            }
            for cat in categories
        ],
    }, indent=2, ensure_ascii=False)
    return render(request, 'helloAssoImporter/cursus_detail.html', {
        'cursus': cursus,
        'categories': categories,
        'cursus_json': cursus_json,
        'active_tab': 'formation',
    })


@admin_required
def cursus_archive(request, pk):
    if request.method == 'POST':
        cursus = get_object_or_404(Cursus, pk=pk)
        if cursus.status == Cursus.Status.ACTIVE:
            cursus.status = Cursus.Status.ARCHIVED
            action_label = "archivé"
        else:
            cursus.status = Cursus.Status.ACTIVE
            action_label = "réactivé"
        cursus.save(update_fields=['status'])
        logger.info("CURSUS_STATUS pk=%s status=%s by=%s", cursus.pk, cursus.status, request.user.username)
        messages.success(request, f"Cursus « {cursus.name} » {action_label}.")
    return redirect('saison-formation')


@admin_required
def cursus_import_json(request):
    error = None
    json_text = ''
    if request.method == 'POST':
        json_text = request.POST.get('json_data', '').strip()
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e:
            error = f"JSON invalide : {e}"
        else:
            name = str(data.get('name', '')).strip()
            date_str = str(data.get('date', '')).strip()
            categories_raw = data.get('categories', [])
            if not name:
                error = "Le champ « name » est obligatoire."
            elif not date_str:
                error = "Le champ « date » est obligatoire (format AAAA-MM-JJ)."
            elif not isinstance(categories_raw, list):
                error = "Le champ « categories » doit être une liste."
            else:
                from datetime import date as date_cls
                try:
                    parsed_date = date_cls.fromisoformat(date_str)
                except ValueError:
                    error = "Format de date invalide (attendu : AAAA-MM-JJ)."
                else:
                    with transaction.atomic():
                        cursus = Cursus.objects.create(name=name, date=parsed_date)
                        for cat_order, cat_data in enumerate(categories_raw, start=1):
                            cat_name = str(cat_data.get('name', '')).strip()
                            if not cat_name:
                                continue
                            category = CursusCategory.objects.create(
                                cursus=cursus, name=cat_name, order=cat_order
                            )
                            for skill_order, skill_data in enumerate(cat_data.get('skills', []), start=1):
                                skill_name = str(skill_data.get('name', '')).strip()
                                if skill_name:
                                    Skill.objects.create(
                                        category=category, name=skill_name, order=skill_order
                                    )
                    logger.info("CURSUS_IMPORT pk=%s name=%s by=%s", cursus.pk, cursus.name, request.user.username)
                    messages.success(request, f"Cursus « {cursus.name} » importé avec succès.")
                    return redirect('saison-cursus-detail', pk=cursus.pk)
    return render(request, 'helloAssoImporter/formation_import.html', {
        'error': error,
        'json_text': json_text,
        'active_tab': 'importer',
    })


@admin_required
def assign_season(request):
    if request.method == 'POST':
        form_slug = request.POST.get('form_slug')
        season_id = request.POST.get('season_id') or None
        try:
            membership_form = MemberShipForm.objects.get(pk=form_slug)
            if season_id and MemberShipForm.objects.filter(season_id=int(season_id)).exclude(pk=form_slug).exists():
                season = Season.objects.get(pk=int(season_id))
                messages.error(request, f"La saison « {season.label} » est déjà associée à un autre formulaire.")
            else:
                membership_form.season_id = int(season_id) if season_id else None
                membership_form.save()
        except MemberShipForm.DoesNotExist:
            pass
    return redirect('saison-formulaires')



@admin_required
def membership_form_detail(request, form_slug):
    membership_form = get_object_or_404(MemberShipForm, pk=form_slug)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save_mapping':
            mapping = {
                field: request.POST.get(field, '').strip()
                for field in {**LEVEL_FIELD_LABELS, **CONTACT_FIELD_LABELS}
                if request.POST.get(field, '').strip()
            }
            membership_form.field_mapping = mapping
            membership_form.save(update_fields=['field_mapping'])
            messages.success(request, "Mapping des champs enregistré.")
        else:
            api = get_hello_asso_api()
            try:
                new_members, new_orders = api.save_membership_form_members(membership_form)
                messages.success(request, f"Import terminé : {new_members} nouveau(x) membre(s), {new_orders} inscription(s) créée(s).")
            except HelloAssoApiError as e:
                notify_api_error(request, e)
    orders = MemberShipFormOrder.objects.filter(form=membership_form).select_related('member').order_by('member__last_name', 'member__first_name')
    level_mapping_rows = [
        (field, label, membership_form.field_mapping.get(field, ''))
        for field, label in LEVEL_FIELD_LABELS.items()
    ]
    cache_key = f'membership_form_fields_{membership_form.form_slug}'
    available_fields = cache.get(cache_key)
    if available_fields is None:
        try:
            available_fields = get_hello_asso_api().get_available_custom_fields(membership_form)
            cache.set(cache_key, available_fields, 300)
        except HelloAssoApiError:
            available_fields = []
    contact_mapping_rows = [
        (field, label, membership_form.field_mapping.get(field, ''))
        for field, label in CONTACT_FIELD_LABELS.items()
    ]
    return render(request, 'helloAssoImporter/membership_form_detail.html', {
        'membership_form': membership_form,
        'orders': orders,
        'level_mapping_rows': level_mapping_rows,
        'contact_mapping_rows': contact_mapping_rows,
        'available_fields': available_fields,
        'active_tab': 'formulaires',
    })


@admin_required
def refresh_membership_forms(request):
    if request.method != 'POST':
        return redirect('saison-formulaires')
    api = get_hello_asso_api()
    try:
        count = api.refresh_membership_forms()
        messages.success(request, f"{count} formulaire(s) d'adhésion importé(s).")
    except HelloAssoApiError as e:
        notify_api_error(request, e)
    return redirect('saison-formulaires')


@admin_required
def member_list(request):
    seasons = Season.objects.order_by('-current', 'label')
    season_id = request.GET.get('season')
    members = Member.objects.prefetch_related('membershipformorder_set__form__season').order_by('last_name', 'first_name')
    if season_id:
        members = members.filter(membershipformorder__form__season_id=season_id)
    members = list(members)
    duplicate_count = (
        Member.objects.values('first_name', 'last_name')
        .annotate(n=Count('id')).filter(n__gt=1).count()
    )
    return render(request, 'helloAssoImporter/member_list.html', {
        'members': members,
        'members_count': len(members),
        'seasons': seasons,
        'current_season_id': season_id,
        'duplicate_count': duplicate_count,
        'active_tab': 'membres',
    })


@admin_required
def member_duplicates(request):
    duplicate_pairs = list(
        Member.objects.values('first_name', 'last_name')
        .annotate(n=Count('id')).filter(n__gt=1)
        .values_list('first_name', 'last_name')
    )
    groups = []
    if duplicate_pairs:
        q = Q()
        for fn, ln in duplicate_pairs:
            q |= Q(first_name=fn, last_name=ln)
        all_members = (
            Member.objects.filter(q)
            .prefetch_related('membershipformorder_set__form__season')
            .order_by('last_name', 'first_name')
        )
        groups = [
            list(group)
            for _, group in itertools_groupby(all_members, key=lambda m: (m.last_name, m.first_name))
        ]
    return render(request, 'helloAssoImporter/member_duplicates.html', {
        'groups': groups,
        'active_tab': 'membres',
    })


@admin_required
def member_detail(request, pk):
    member = get_object_or_404(Member, pk=pk)

    email_error = None
    birthdate_error = None
    cert_error = None
    if request.method == 'POST':
        action = request.POST.get('_action')

        if action == 'email':
            new_email = request.POST.get('email', '').strip().lower()
            if not new_email:
                email_error = "L'adresse email ne peut pas être vide."
            elif Member.objects.filter(
                email=new_email, first_name=member.first_name, last_name=member.last_name
            ).exclude(pk=pk).exists():
                email_error = "Un membre avec ce nom et cet email existe déjà."
            else:
                if new_email != member.email:
                    old_email = member.email
                    member.email = new_email
                    member.save(update_fields=['email'])
                    logger.info("MEMBER_EMAIL_CHANGE pk=%s old=%s new=%s by=%s",
                                member.pk, old_email, new_email, request.user.username)
                return redirect('saison-membre-detail', pk=pk)

        elif action == 'birthdate':
            from datetime import date
            raw_birthdate = request.POST.get('birthdate', '').strip()
            new_birthdate = None
            if raw_birthdate:
                try:
                    new_birthdate = date.fromisoformat(raw_birthdate)
                except ValueError:
                    birthdate_error = "Format de date invalide (attendu : AAAA-MM-JJ)."
            if not birthdate_error:
                if new_birthdate != member.birthdate:
                    member.birthdate = new_birthdate
                    member.save(update_fields=['birthdate'])
                    logger.info("MEMBER_BIRTHDATE_CHANGE pk=%s date=%s by=%s",
                                member.pk, new_birthdate, request.user.username)
                return redirect('saison-membre-detail', pk=pk)

        elif action == 'formation_add':
            cursus_id = request.POST.get('cursus_id')
            if cursus_id:
                cursus = get_object_or_404(Cursus, pk=cursus_id)
                member.formations.add(cursus)
                logger.info("MEMBER_FORMATION_ADD pk=%s cursus=%s by=%s", member.pk, cursus_id, request.user.username)
            return redirect('saison-membre-detail', pk=pk)

        elif action == 'formation_remove':
            cursus_id = request.POST.get('cursus_id')
            if cursus_id:
                member.formations.remove(cursus_id)
                logger.info("MEMBER_FORMATION_REMOVE pk=%s cursus=%s by=%s", member.pk, cursus_id, request.user.username)
            return redirect('saison-membre-detail', pk=pk)

        elif action == 'cert':
            from datetime import date
            raw_cert = request.POST.get('medical_certificate_date', '').strip()
            new_cert = None
            if raw_cert:
                try:
                    new_cert = date.fromisoformat(raw_cert)
                except ValueError:
                    cert_error = "Format de date invalide (attendu : AAAA-MM-JJ)."
            if not cert_error:
                if new_cert != member.medical_certificate_date:
                    member.medical_certificate_date = new_cert
                    member.save(update_fields=['medical_certificate_date'])
                    logger.info("MEMBER_CERT_CHANGE pk=%s date=%s by=%s",
                                member.pk, new_cert, request.user.username)
                return redirect('saison-membre-detail', pk=pk)

    orders = MemberShipFormOrder.objects.filter(member=member).select_related('form__season').order_by('form__start_date')
    query = request.GET.get('q', '').strip()
    candidates = []
    if query:
        candidates = (
            Member.objects
            .filter(Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(email__icontains=query))
            .exclude(pk=pk)
            .prefetch_related('membershipformorder_set__form__season')
        )
    member_formations = member.formations.all()
    available_cursus = Cursus.objects.filter(status=Cursus.Status.ACTIVE).exclude(pk__in=member_formations)
    return render(request, 'helloAssoImporter/member_detail.html', {
        'member': member,
        'orders': orders,
        'query': query,
        'candidates': candidates,
        'email_error': email_error,
        'birthdate_error': birthdate_error,
        'cert_error': cert_error,
        'member_formations': member_formations,
        'available_cursus': available_cursus,
        'active_tab': 'membres',
    })


@admin_required
def member_merge(request):
    if request.method != 'POST':
        return redirect('saison-membres-doublons')

    keep_id = request.POST.get('keep_id')
    merge_ids = request.POST.getlist('merge_ids')

    if not keep_id or not merge_ids:
        messages.error(request, "Données de fusion invalides.")
        return redirect('saison-membres-doublons')

    try:
        with transaction.atomic():
            keep = Member.objects.get(pk=keep_id)
            to_merge = list(Member.objects.filter(pk__in=merge_ids))
            existing_form_ids = set(
                MemberShipFormOrder.objects.filter(member=keep).values_list('form_id', flat=True)
            )
            to_update = []
            for source in to_merge:
                for order in source.membershipformorder_set.all():
                    if order.form_id not in existing_form_ids:
                        order.member = keep
                        to_update.append(order)
                        existing_form_ids.add(order.form_id)
            if to_update:
                MemberShipFormOrder.objects.bulk_update(to_update, ['member'])
            for source in to_merge:
                source.delete()
            reassigned = len(to_update)
        logger.info("MEMBER_MERGE keep=pk:%s (%s %s) absorbed=%s reassigned=%d by=%s",
                    keep.pk, keep.first_name, keep.last_name,
                    [m.pk for m in to_merge], reassigned, request.user.username)
        messages.success(request, f"Fusion effectuée : {keep.first_name} {keep.last_name} conservé, {reassigned} inscription(s) réassignée(s).")
    except Member.DoesNotExist:
        messages.error(request, "Membre introuvable.")

    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect('saison-membres-doublons')


class AdherentListView(ClubStaffRequiredMixin, ListView):
    model = Member
    template_name = 'helloAssoImporter/adherent_list.html'
    context_object_name = 'members'

    def get_queryset(self):
        return (
            Member.objects
            .filter(membershipformorder__form__season__current=True)
            .distinct()
            .order_by('last_name', 'first_name')
        )


@club_staff_required
def adherent_detail(request, pk):
    member = get_object_or_404(
        Member.objects.filter(membershipformorder__form__season__current=True).distinct(),
        pk=pk,
    )
    current_order = (
        MemberShipFormOrder.objects
        .filter(member=member, form__season__current=True)
        .first()
    )
    formations_qs = member.formations.prefetch_related('categories__skills').all()
    skill_ids = [s.pk for f in formations_qs for c in f.categories.all() for s in c.skills.all()]
    member_skills_map = {
        ms.skill_id: ms.status
        for ms in MemberSkill.objects.filter(member=member, skill_id__in=skill_ids)
    }
    evaluations_map = {}
    for ev in SkillEvaluation.objects.filter(member=member, skill_id__in=skill_ids).select_related('author').order_by('-date', '-pk'):
        evaluations_map.setdefault(ev.skill_id, []).append({
            'pk': ev.pk,
            'author': (ev.author.get_full_name() or ev.author.username) if ev.author else '',
            'date': ev.date,
            'status': ev.status,
            'status_display': ev.get_status_display(),
            'comment': ev.comment,
        })
    status_choices = MemberSkill.SkillStatus.choices
    formations = []
    for cursus in formations_qs:
        categories = []
        for category in cursus.categories.all():
            skills = [
                {
                    'pk': s.pk,
                    'name': s.name,
                    'status': member_skills_map.get(s.pk, MemberSkill.SkillStatus.NOT_ACQUIRED),
                    'evaluations': evaluations_map.get(s.pk, []),
                }
                for s in category.skills.all()
            ]
            categories.append({'name': category.name, 'skills': skills})
        formations.append({'pk': cursus.pk, 'name': cursus.name, 'categories': categories})
    return render(request, 'helloAssoImporter/adherent_detail.html', {
        'member': member,
        'current_order': current_order,
        'formations': formations,
        'status_choices': status_choices,
    })


@club_staff_required
def adherent_formation_save(request, pk, cursus_pk):
    if request.method != 'POST':
        return redirect('adherent-detail', pk=pk)
    from django.utils import timezone as tz
    member = get_object_or_404(
        Member.objects.filter(membershipformorder__form__season__current=True).distinct(),
        pk=pk,
    )
    cursus = get_object_or_404(Cursus, pk=cursus_pk)
    valid_statuses = set(dict(MemberSkill.SkillStatus.choices))
    skills = Skill.objects.filter(category__cursus=cursus)
    today = tz.localdate()
    current = {
        ms.skill_id: ms.status
        for ms in MemberSkill.objects.filter(member=member, skill__in=skills)
    }
    for skill in skills:
        status = request.POST.get(f'skill_{skill.pk}_status', MemberSkill.SkillStatus.NOT_ACQUIRED)
        if status not in valid_statuses:
            status = MemberSkill.SkillStatus.NOT_ACQUIRED
        comment = request.POST.get(f'skill_{skill.pk}_comment', '').strip()
        MemberSkill.objects.update_or_create(
            member=member, skill=skill,
            defaults={'status': status},
        )
        if comment or status != current.get(skill.pk, MemberSkill.SkillStatus.NOT_ACQUIRED):
            SkillEvaluation.objects.create(
                member=member, skill=skill,
                date=today, status=status, comment=comment,
                author=request.user,
            )
    return redirect(reverse('adherent-detail', args=[pk]) + '?tab=formation')


@club_staff_required
def adherent_formation_export(request, pk, cursus_pk):
    import io
    from django.http import HttpResponse
    from django.utils.formats import date_format
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    member = get_object_or_404(
        Member.objects.filter(membershipformorder__form__season__current=True).distinct(),
        pk=pk,
    )
    cursus = get_object_or_404(Cursus, pk=cursus_pk)

    categories = (
        cursus.categories
        .prefetch_related('skills__member_skills', 'skills__evaluations')
        .all()
    )
    member_skills = {
        ms.skill_id: ms
        for ms in MemberSkill.objects.filter(member=member, skill__category__cursus=cursus)
    }
    evaluations_by_skill = {}
    for ev in SkillEvaluation.objects.filter(member=member, skill__category__cursus=cursus).select_related('author').order_by('-date'):
        evaluations_by_skill.setdefault(ev.skill_id, []).append(ev)

    STATUS_LABELS = {
        MemberSkill.SkillStatus.NOT_ACQUIRED: 'Non acquis',
        MemberSkill.SkillStatus.IN_PROGRESS:  "En cours",
        MemberSkill.SkillStatus.ACQUIRED:     'Acquis',
    }

    styles = getSampleStyleSheet()
    small = ParagraphStyle('small', parent=styles['Normal'], fontSize=8, leading=10)
    small_grey = ParagraphStyle('small_grey', parent=small, textColor=colors.HexColor('#6b7280'))
    bold_cat = ParagraphStyle('bold_cat', parent=styles['Normal'], fontSize=9, leading=11, fontName='Helvetica-Bold')
    header_style = ParagraphStyle('header', parent=styles['Normal'], fontSize=8, leading=10, fontName='Helvetica-Bold')

    COL_WIDTHS = [8 * cm, 3 * cm, 7.5 * cm]

    table_data = [[
        Paragraph('Compétence', header_style),
        Paragraph('Statut', header_style),
        Paragraph('Commentaires', header_style),
    ]]

    row_styles = []
    current_row = 1

    for category in categories:
        table_data.append([
            Paragraph(category.name, bold_cat),
            '', '',
        ])
        row_styles.append(('BACKGROUND', (0, current_row), (-1, current_row), colors.HexColor('#f3f4f6')))
        row_styles.append(('SPAN', (0, current_row), (-1, current_row)))
        current_row += 1

        for skill in category.skills.all():
            ms = member_skills.get(skill.pk)
            status_label = STATUS_LABELS.get(ms.status if ms else None, 'Non acquis')
            evs = evaluations_by_skill.get(skill.pk, [])

            comment_lines = []
            for ev in evs:
                date_str = date_format(ev.date, 'd/m/Y')
                if ev.author:
                    date_str += f" · {escape(ev.author.get_full_name() or ev.author.username)}"
                line = f"<font color='#9ca3af'>{date_str}</font>"
                comment_lines.append(f"{line} {escape(ev.comment)}" if ev.comment else line)
            comment_para = Paragraph('<br/>'.join(comment_lines), small_grey) if comment_lines else ''

            table_data.append([
                Paragraph(skill.name, small),
                Paragraph(status_label, small),
                comment_para,
            ])
            current_row += 1

    table = Table(table_data, colWidths=COL_WIDTHS, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, 0),  colors.HexColor('#1a1a2e')),
        ('TEXTCOLOR',    (0, 0), (-1, 0),  colors.white),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
        ('GRID',         (0, 0), (-1, -1), 0.4, colors.HexColor('#e5e7eb')),
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',   (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
        ('LEFTPADDING',  (0, 0), (-1, -1), 6),
        *row_styles,
    ]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
    )

    title_style = ParagraphStyle('title', parent=styles['Heading1'], fontSize=14, spaceAfter=12)

    story = [
        Paragraph(cursus.name, title_style),
        table,
    ]
    doc.build(story)

    buf.seek(0)
    raw_name = f"evaluation_{member.last_name}_{member.first_name}_{cursus.name}.pdf"
    safe_ascii = raw_name.replace('"', '').replace('\n', '').replace('\r', '')
    response = HttpResponse(buf, content_type='application/pdf')
    response['Content-Disposition'] = (
        f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{quote(raw_name)}"
    )
    return response


@club_staff_required
def adherent_evaluation_delete(request, pk, ev_pk):
    if request.method != 'POST':
        return redirect('adherent-detail', pk=pk)
    ev = get_object_or_404(SkillEvaluation, pk=ev_pk, member__pk=pk)
    cursus_pk = ev.skill.category.cursus_id
    ev.delete()
    return redirect(reverse('adherent-detail', args=[pk]) + '?tab=formation')


@club_staff_required
def inscriptions_member_list(request):
    members = Member.objects.prefetch_related(
        'membershipformorder_set__form__season'
    ).order_by('last_name', 'first_name')
    return render(request, 'helloAssoImporter/inscriptions_member_list.html', {
        'members': members,
        'active_tab': 'membres',
    })


@club_staff_required
def inscriptions_member_detail(request, pk):
    member = get_object_or_404(Member, pk=pk)
    current_order = (
        MemberShipFormOrder.objects
        .filter(member=member, form__season__current=True)
        .select_related('form__season')
        .first()
    )
    return render(request, 'helloAssoImporter/inscriptions_member_detail.html', {
        'member': member,
        'current_order': current_order,
        'active_tab': 'membres',
    })


@club_staff_required
def create_event_form(request):
    if request.method == 'POST':
        form = EventFormCreateForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            api = get_hello_asso_api()
            try:
                api.create_event_form(d)
                messages.success(request, f"Sortie « {d['title']} » créée avec succès.")
                return redirect('inscriptions')
            except HelloAssoApiError as e:
                logger.error("HelloAsso API error (create_event_form): %s", e)
                messages.error(request, "Échec de la création de la sortie. Veuillez réessayer.")
    else:
        form = EventFormCreateForm()
    return render(request, 'helloAssoImporter/event_form_create.html', {'form': form})


_REFRESH_COOLDOWN_KEY = 'event_refresh_cooldown'
_REFRESH_COOLDOWN_SECONDS = 60


@club_staff_required
def refresh_event_forms(request):
    if request.method != 'POST':
        return redirect('inscriptions')
    if cache.get(_REFRESH_COOLDOWN_KEY):
        messages.warning(request, "Rafraîchissement déjà effectué récemment. Veuillez patienter 1 minute.")
        return redirect('inscriptions')
    cache.set(_REFRESH_COOLDOWN_KEY, True, _REFRESH_COOLDOWN_SECONDS)
    api = get_hello_asso_api()
    start = time.time()
    try:
        forms_added = api.refresh_event_forms()
    except HelloAssoApiError as e:
        cache.delete(_REFRESH_COOLDOWN_KEY)
        notify_api_error(request, e)
        return redirect('inscriptions')
    registrations_added = 0
    for form in EventForm.objects.all():
        try:
            since = form.last_registration_updated - timedelta(hours=24) if form.last_registration_updated else None
            registrations_added += api.get_event_form_orders(form, since=since)
        except HelloAssoApiError as e:
            notify_api_error(request, e)
    elapsed = round(time.time() - start)
    messages.success(request, f"Import terminé. {forms_added} sortie(s) et {registrations_added} inscription(s) créées en {elapsed} seconde(s).")
    return redirect('inscriptions')
