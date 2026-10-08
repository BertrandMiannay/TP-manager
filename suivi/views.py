import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from helloAssoImporter.models import Cursus, Member, MemberSkill, Season, Skill, SkillEvaluation
from userManagement.views import club_staff_required
from .forms import ExerciseForm, StudentNoteForm, TrainingSessionForm
from .models import Attendance, Exercise, StudentNote, TrainingSession

logger = logging.getLogger(__name__)


def _current_season():
    return Season.objects.filter(current=True).first()


def _students(season=None, cursus_id=None):
    """Élèves = adhérents de la saison inscrits à au moins une formation."""
    qs = Member.objects.filter(formations__isnull=False)
    if season:
        qs = qs.filter(membershipformorder__form__season=season)
    if cursus_id:
        qs = qs.filter(formations=cursus_id)
    return qs.distinct().order_by('last_name', 'first_name')


def _attendance_stats(attendances):
    stats = {'present': 0, 'absent': 0, 'excused': 0}
    for a in attendances:
        stats[a.status] = stats.get(a.status, 0) + 1
    stats['total'] = stats['present'] + stats['absent'] + stats['excused']
    stats['rate'] = round(100 * stats['present'] / stats['total']) if stats['total'] else None
    return stats


def _skills_progress(member, attended_session_ids=None):
    """Structure cursus → catégories → compétences, avec statut, nb de séances où la compétence
    a été travaillée en présence de l'élève et dernière évaluation."""
    formations = member.formations.prefetch_related('categories__skills').order_by('name')
    skill_ids = [s.pk for f in formations for c in f.categories.all() for s in c.skills.all()]
    status_map = {
        ms.skill_id: ms.status
        for ms in MemberSkill.objects.filter(member=member, skill_id__in=skill_ids)
    }
    worked_map = {}
    if attended_session_ids is not None:
        for row in (
            TrainingSession.skills.through.objects
            .filter(trainingsession_id__in=attended_session_ids, skill_id__in=skill_ids)
            .values('skill_id').annotate(n=Count('trainingsession_id'))
        ):
            worked_map[row['skill_id']] = row['n']
    last_eval = {}
    for ev in SkillEvaluation.objects.filter(member=member, skill_id__in=skill_ids).order_by('-date', '-pk'):
        last_eval.setdefault(ev.skill_id, ev)

    result = []
    for cursus in formations:
        categories = []
        counts = {'acquired': 0, 'in_progress': 0, 'not_acquired': 0, 'worked': 0, 'total': 0}
        for category in cursus.categories.all():
            skills = []
            for s in category.skills.all():
                status = status_map.get(s.pk, MemberSkill.SkillStatus.NOT_ACQUIRED)
                worked = worked_map.get(s.pk, 0)
                counts[status] += 1
                counts['total'] += 1
                if worked or status != MemberSkill.SkillStatus.NOT_ACQUIRED:
                    counts['worked'] += 1
                skills.append({
                    'pk': s.pk,
                    'name': s.name,
                    'status': status,
                    'status_display': MemberSkill.SkillStatus(status).label,
                    'worked': worked,
                    'last_eval': last_eval.get(s.pk),
                })
            categories.append({'name': category.name, 'skills': skills})
        counts['acquired_pct'] = round(100 * counts['acquired'] / counts['total']) if counts['total'] else 0
        result.append({'pk': cursus.pk, 'name': cursus.name, 'categories': categories, 'counts': counts})
    return result


# ---------------------------------------------------------------------------
# Séances
# ---------------------------------------------------------------------------

@club_staff_required
def session_list(request):
    seasons = Season.objects.order_by('-current', 'label')
    season_id = request.GET.get('season')
    season = Season.objects.filter(pk=season_id).first() if season_id else _current_season()
    sessions = TrainingSession.objects.all()
    if season:
        sessions = sessions.filter(season=season)
    sessions = sessions.prefetch_related('cursus', 'instructors').annotate(
        present_count=Count('attendances', filter=Q(attendances__status=Attendance.Status.PRESENT), distinct=True),
        absent_count=Count('attendances', filter=~Q(attendances__status=Attendance.Status.PRESENT), distinct=True),
        skill_count=Count('skills', distinct=True),
    )
    return render(request, 'suivi/session_list.html', {
        'sessions': sessions,
        'seasons': seasons,
        'season': season,
        'active_tab': 'seances',
    })


@club_staff_required
def session_create(request):
    if request.method == 'POST':
        form = TrainingSessionForm(request.POST)
        if form.is_valid():
            session = form.save(commit=False)
            session.season = _current_season()
            session.save()
            form.save_m2m()
            logger.info("SESSION_CREATE pk=%s date=%s by=%s", session.pk, session.date, request.user.username)
            messages.success(request, f"{session} créée.")
            return redirect('suivi-session-detail', pk=session.pk)
    else:
        initial = {'date': timezone.localdate()}
        if request.user.is_club_staff:
            initial['instructors'] = [request.user.pk]
        form = TrainingSessionForm(initial=initial)
    return render(request, 'suivi/session_form.html', {
        'form': form,
        'active_tab': 'seances',
    })


@club_staff_required
def session_detail(request, pk):
    session = get_object_or_404(TrainingSession, pk=pk)

    if request.method == 'POST':
        action = request.POST.get('_action')

        if action == 'edit':
            form = TrainingSessionForm(request.POST, instance=session)
            if form.is_valid():
                form.save()
                messages.success(request, "Séance mise à jour.")
            else:
                messages.error(request, "Formulaire invalide : " + "; ".join(
                    f"{form.fields[f].label if f in form.fields else f} : {', '.join(e)}" for f, e in form.errors.items()
                ))
            return redirect('suivi-session-detail', pk=pk)

        elif action == 'attendance':
            valid = set(Attendance.Status.values)
            with transaction.atomic():
                for member in session.roster():
                    status = request.POST.get(f'att_{member.pk}', '')
                    comment = request.POST.get(f'att_{member.pk}_comment', '').strip()[:255]
                    if status in valid:
                        Attendance.objects.update_or_create(
                            session=session, member=member,
                            defaults={'status': status, 'comment': comment},
                        )
                    else:
                        Attendance.objects.filter(session=session, member=member).delete()
            messages.success(request, "Présences enregistrées.")
            return redirect(reverse('suivi-session-detail', args=[pk]) + '#presences')

        elif action == 'add_member':
            member = get_object_or_404(Member, pk=request.POST.get('member_id'))
            Attendance.objects.get_or_create(
                session=session, member=member, defaults={'status': Attendance.Status.PRESENT},
            )
            return redirect(reverse('suivi-session-detail', args=[pk]) + '#presences')

        elif action == 'skills':
            allowed = Skill.objects.filter(category__cursus__in=session.cursus.all())
            ids = request.POST.getlist('skill_ids')
            session.skills.set(allowed.filter(pk__in=ids))
            messages.success(request, "Compétences travaillées enregistrées.")
            return redirect(reverse('suivi-session-detail', args=[pk]) + '#competences')

        elif action == 'evaluations':
            valid = set(MemberSkill.SkillStatus.values)
            present = Member.objects.filter(
                attendances__session=session, attendances__status=Attendance.Status.PRESENT,
            )
            skills = list(session.skills.all())
            changed = 0
            with transaction.atomic():
                for member in present:
                    current = {
                        ms.skill_id: ms.status
                        for ms in MemberSkill.objects.filter(member=member, skill__in=skills)
                    }
                    for skill in skills:
                        status = request.POST.get(f'eval_{member.pk}_{skill.pk}', '')
                        comment = request.POST.get(f'eval_{member.pk}_{skill.pk}_comment', '').strip()
                        if status not in valid:
                            continue
                        old = current.get(skill.pk, MemberSkill.SkillStatus.NOT_ACQUIRED)
                        if status == old and not comment:
                            continue
                        MemberSkill.objects.update_or_create(
                            member=member, skill=skill, defaults={'status': status},
                        )
                        SkillEvaluation.objects.create(
                            member=member, skill=skill, date=session.date, status=status,
                            comment=comment or f"Séance du {session.date:%d/%m/%Y}",
                        )
                        changed += 1
            messages.success(request, f"{changed} évaluation(s) enregistrée(s).")
            return redirect(reverse('suivi-session-detail', args=[pk]) + '#evaluations')

        elif action == 'note':
            member = get_object_or_404(Member, pk=request.POST.get('member_id'))
            content = request.POST.get('content', '').strip()
            visibility = request.POST.get('visibility')
            if visibility not in StudentNote.Visibility.values:
                visibility = StudentNote.Visibility.STAFF
            if content:
                StudentNote.objects.create(
                    member=member, session=session, date=session.date,
                    content=content, visibility=visibility, author=request.user,
                )
                messages.success(request, f"Note ajoutée pour {member.first_name} {member.last_name}.")
            return redirect(reverse('suivi-session-detail', args=[pk]) + '#notes')

    roster = list(session.roster())
    attendance_map = {a.member_id: a for a in session.attendances.all()}
    roster_rows = [{'member': m, 'attendance': attendance_map.get(m.pk)} for m in roster]
    stats = _attendance_stats(attendance_map.values())

    session_cursus = session.cursus.prefetch_related('categories__skills').order_by('name')
    selected_skill_ids = set(session.skills.values_list('pk', flat=True))
    skill_tree = [
        {
            'name': c.name,
            'categories': [
                {
                    'name': cat.name,
                    'skills': [{'pk': s.pk, 'name': s.name, 'selected': s.pk in selected_skill_ids} for s in cat.skills.all()],
                }
                for cat in c.categories.all()
            ],
        }
        for c in session_cursus
    ]

    session_skills = list(session.skills.select_related('category__cursus').order_by('category__cursus__name', 'category__order', 'order'))
    present_members = [r['member'] for r in roster_rows if r['attendance'] and r['attendance'].status == Attendance.Status.PRESENT]
    status_lookup = {
        (ms.member_id, ms.skill_id): ms.status
        for ms in MemberSkill.objects.filter(member__in=present_members, skill__in=session_skills)
    }
    eval_rows = []
    for m in present_members:
        enrolled = set(m.formations.values_list('pk', flat=True))
        eval_rows.append({
            'member': m,
            'skills': [
                {
                    'skill': s,
                    'status': status_lookup.get((m.pk, s.pk), MemberSkill.SkillStatus.NOT_ACQUIRED),
                }
                for s in session_skills if s.category.cursus_id in enrolled
            ],
        })

    roster_ids = [m.pk for m in roster]
    addable_members = (
        Member.objects.filter(membershipformorder__form__season=session.season) if session.season_id else Member.objects.all()
    ).exclude(pk__in=roster_ids).distinct().order_by('last_name', 'first_name')

    return render(request, 'suivi/session_detail.html', {
        'session': session,
        'form': TrainingSessionForm(instance=session),
        'roster_rows': roster_rows,
        'stats': stats,
        'attendance_choices': Attendance.Status.choices,
        'skill_tree': skill_tree,
        'session_skills': session_skills,
        'eval_rows': eval_rows,
        'status_choices': MemberSkill.SkillStatus.choices,
        'visibility_choices': StudentNote.Visibility.choices,
        'session_notes': session.student_notes.select_related('member', 'author'),
        'addable_members': addable_members,
        'active_tab': 'seances',
    })


@club_staff_required
def session_delete(request, pk):
    if request.method == 'POST':
        session = get_object_or_404(TrainingSession, pk=pk)
        logger.info("SESSION_DELETE pk=%s date=%s by=%s", session.pk, session.date, request.user.username)
        messages.success(request, f"{session} supprimée.")
        session.delete()
    return redirect('suivi-sessions')


# ---------------------------------------------------------------------------
# Présences (vue d'ensemble)
# ---------------------------------------------------------------------------

@club_staff_required
def attendance_overview(request):
    season = _current_season()
    cursus_list = Cursus.objects.filter(status=Cursus.Status.ACTIVE).order_by('name')
    cursus_id = request.GET.get('cursus') or None
    sessions = TrainingSession.objects.filter(season=season).order_by('date', 'pk')
    if cursus_id:
        sessions = sessions.filter(cursus=cursus_id)
    sessions = list(sessions)
    students = list(_students(season, cursus_id))
    att_map = {
        (a.member_id, a.session_id): a
        for a in Attendance.objects.filter(session__in=sessions, member__in=students)
    }
    rows = []
    for m in students:
        cells = [att_map.get((m.pk, s.pk)) for s in sessions]
        rows.append({
            'member': m,
            'cells': cells,
            'stats': _attendance_stats([c for c in cells if c]),
        })
    return render(request, 'suivi/attendance_overview.html', {
        'season': season,
        'sessions': sessions,
        'rows': rows,
        'cursus_list': cursus_list,
        'cursus_id': int(cursus_id) if cursus_id and cursus_id.isdigit() else None,
        'active_tab': 'presences',
    })


# ---------------------------------------------------------------------------
# Élèves
# ---------------------------------------------------------------------------

@club_staff_required
def student_list(request):
    season = _current_season()
    cursus_list = Cursus.objects.filter(status=Cursus.Status.ACTIVE).order_by('name')
    cursus_id = request.GET.get('cursus') or None
    students = _students(season, cursus_id).prefetch_related('formations')
    season_filter = Q(attendances__session__season=season) if season else Q()
    students = students.annotate(
        present_count=Count('attendances', filter=season_filter & Q(attendances__status=Attendance.Status.PRESENT), distinct=True),
        attendance_count=Count('attendances', filter=season_filter, distinct=True),
        acquired_count=Count('member_skills', filter=Q(member_skills__status=MemberSkill.SkillStatus.ACQUIRED), distinct=True),
    )
    rows = []
    for m in students:
        rate = round(100 * m.present_count / m.attendance_count) if m.attendance_count else None
        rows.append({'member': m, 'rate': rate})
    return render(request, 'suivi/student_list.html', {
        'season': season,
        'rows': rows,
        'cursus_list': cursus_list,
        'cursus_id': int(cursus_id) if cursus_id and cursus_id.isdigit() else None,
        'active_tab': 'eleves',
    })


@club_staff_required
def student_detail(request, pk):
    member = get_object_or_404(Member, pk=pk)
    exercise_form = ExerciseForm(member=member, initial={'date': timezone.localdate()})
    note_form = StudentNoteForm(member=member, initial={'date': timezone.localdate()})
    open_form = None

    if request.method == 'POST':
        action = request.POST.get('_action')

        if action == 'add_exercise':
            exercise_form = ExerciseForm(request.POST, member=member)
            if exercise_form.is_valid():
                ex = exercise_form.save(commit=False)
                ex.member = member
                ex.author = request.user
                ex.save()
                messages.success(request, "Exercice ajouté.")
                return redirect(reverse('suivi-student-detail', args=[pk]) + '#exercices')
            open_form = 'exercise'

        elif action == 'delete_exercise':
            Exercise.objects.filter(pk=request.POST.get('exercise_pk'), member=member).delete()
            return redirect(reverse('suivi-student-detail', args=[pk]) + '#exercices')

        elif action == 'add_note':
            note_form = StudentNoteForm(request.POST, member=member)
            if note_form.is_valid():
                note = note_form.save(commit=False)
                note.member = member
                note.author = request.user
                note.save()
                messages.success(request, "Note ajoutée.")
                return redirect(reverse('suivi-student-detail', args=[pk]) + '#notes')
            open_form = 'note'

        elif action == 'toggle_note':
            note = get_object_or_404(StudentNote, pk=request.POST.get('note_pk'), member=member)
            note.visibility = (
                StudentNote.Visibility.STAFF if note.visibility == StudentNote.Visibility.STUDENT
                else StudentNote.Visibility.STUDENT
            )
            note.save(update_fields=['visibility'])
            return redirect(reverse('suivi-student-detail', args=[pk]) + '#notes')

        elif action == 'delete_note':
            StudentNote.objects.filter(pk=request.POST.get('note_pk'), member=member).delete()
            return redirect(reverse('suivi-student-detail', args=[pk]) + '#notes')

    season = _current_season()
    attendances = member.attendances.select_related('session').prefetch_related('session__skills')
    if season:
        attendances = attendances.filter(session__season=season)
    attendances = list(attendances.order_by('-session__date', '-session__pk'))
    attended_ids = [a.session_id for a in attendances if a.status == Attendance.Status.PRESENT]

    is_current_member = member.membershipformorder_set.filter(form__season__current=True).exists()

    return render(request, 'suivi/student_detail.html', {
        'member': member,
        'attendances': attendances,
        'stats': _attendance_stats(attendances),
        'formations': _skills_progress(member, attended_ids),
        'exercises': member.exercises.select_related('skill', 'session', 'author'),
        'notes': member.student_notes.select_related('session', 'author'),
        'exercise_form': exercise_form,
        'note_form': note_form,
        'open_form': open_form,
        'is_current_member': is_current_member,
        'season': season,
        'active_tab': 'eleves',
    })


# ---------------------------------------------------------------------------
# Espace élève
# ---------------------------------------------------------------------------

@login_required
def my_tracking(request):
    """Fiche de suivi de l'élève connecté (rapprochement par adresse email)."""
    email = (request.user.email or '').strip()
    members = Member.objects.filter(email__iexact=email).order_by('first_name') if email else Member.objects.none()
    season = _current_season()
    sheets = []
    for member in members:
        attendances = member.attendances.select_related('session')
        if season:
            attendances = attendances.filter(session__season=season)
        attendances = list(attendances.order_by('-session__date'))
        attended_ids = [a.session_id for a in attendances if a.status == Attendance.Status.PRESENT]
        sheets.append({
            'member': member,
            'attendances': attendances,
            'stats': _attendance_stats(attendances),
            'formations': _skills_progress(member, attended_ids),
            'exercises': member.exercises.select_related('skill', 'session'),
            'notes': member.student_notes.filter(visibility=StudentNote.Visibility.STUDENT).select_related('session', 'author'),
        })
    return render(request, 'suivi/my_tracking.html', {
        'sheets': sheets,
        'season': season,
    })
