from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from helloAssoImporter.models import (
    Cursus, CursusCategory, Member, MemberShipForm, MemberShipFormOrder, MemberSkill, Season, Skill, SkillEvaluation,
)
from .models import Attendance, Exercise, StudentNote, TrainingSession


class SuiviTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        for name in ['member', 'instructor', 'dive_director', 'admin']:
            Group.objects.get_or_create(name=name)
        cls.instructor = User.objects.create_user('moniteur', 'moniteur@example.com', 'pwd')
        cls.instructor.groups.add(Group.objects.get(name='instructor'))
        cls.student_user = User.objects.create_user('eleve', 'alice@example.com', 'pwd')
        cls.student_user.groups.add(Group.objects.get(name='member'))

        cls.season = Season.objects.create(label='2026-2027', current=True)
        now = timezone.now()
        form = MemberShipForm.objects.create(
            form_slug='adhesion', title='Adhésion', form_type='Membership', description='',
            start_date=now, end_date=now, updated_at=now, created_at=now, season=cls.season,
        )
        cls.cursus = Cursus.objects.create(name='Niveau 1', date=date(2026, 1, 1))
        other_cursus = Cursus.objects.create(name='Niveau 2', date=date(2026, 1, 1))
        cat = CursusCategory.objects.create(cursus=cls.cursus, name='Technique')
        cls.skill_a = Skill.objects.create(category=cat, name='Vidage de masque', order=1)
        cls.skill_b = Skill.objects.create(category=cat, name='Lâcher / reprise embout', order=2)

        cls.alice = Member.objects.create(email='alice@example.com', first_name='Alice', last_name='Martin')
        cls.bob = Member.objects.create(email='bob@example.com', first_name='Bob', last_name='Durand')
        cls.carol = Member.objects.create(email='carol@example.com', first_name='Carol', last_name='Petit')
        cls.alice.formations.add(cls.cursus)
        cls.bob.formations.add(cls.cursus)
        cls.carol.formations.add(other_cursus)
        for i, m in enumerate([cls.alice, cls.bob, cls.carol], start=1):
            MemberShipFormOrder.objects.create(
                item_id=i, order_id=i, form=form, member=m, payer_email=m.email,
                payer_first_name=m.first_name, payer_last_name=m.last_name, updated_at=now, created_at=now,
            )

    def setUp(self):
        self.client.force_login(self.instructor)

    def _create_session(self):
        resp = self.client.post(reverse('suivi-session-create'), {
            'date': '2026-10-07', 'title': 'Piscine', 'location': 'Bassin', 'cursus': [self.cursus.pk],
            'instructors': [self.instructor.pk], 'notes': '',
        })
        session = TrainingSession.objects.get()
        self.assertRedirects(resp, reverse('suivi-session-detail', args=[session.pk]))
        return session

    def test_session_create_and_roster(self):
        session = self._create_session()
        self.assertEqual(session.season, self.season)
        self.assertEqual(list(session.roster()), [self.bob, self.alice])
        resp = self.client.get(reverse('suivi-session-detail', args=[session.pk]))
        self.assertContains(resp, 'Alice')
        self.assertNotContains(resp, 'name="att_%d"' % self.carol.pk)

    def test_attendance_skills_and_evaluations(self):
        session = self._create_session()
        url = reverse('suivi-session-detail', args=[session.pk])
        self.client.post(url, {
            '_action': 'attendance',
            f'att_{self.alice.pk}': 'present',
            f'att_{self.bob.pk}': 'excused', f'att_{self.bob.pk}_comment': 'malade',
        })
        self.assertEqual(Attendance.objects.get(member=self.alice).status, 'present')
        self.assertEqual(Attendance.objects.get(member=self.bob).comment, 'malade')

        # Un participant hors niveau peut être ajouté
        self.client.post(url, {'_action': 'add_member', 'member_id': self.carol.pk})
        self.assertIn(self.carol, session.roster())

        self.client.post(url, {'_action': 'skills', 'skill_ids': [self.skill_a.pk, self.skill_b.pk]})
        self.assertEqual(set(session.skills.all()), {self.skill_a, self.skill_b})

        resp = self.client.post(url, {
            '_action': 'evaluations',
            f'eval_{self.alice.pk}_{self.skill_a.pk}': 'acquired',
            f'eval_{self.alice.pk}_{self.skill_b.pk}': 'not_acquired',
            f'eval_{self.bob.pk}_{self.skill_a.pk}': 'acquired',  # absent : ignoré
        })
        self.assertEqual(MemberSkill.objects.get(member=self.alice, skill=self.skill_a).status, 'acquired')
        self.assertFalse(MemberSkill.objects.filter(member=self.bob).exists())
        self.assertEqual(SkillEvaluation.objects.filter(member=self.alice).count(), 1)
        self.assertEqual(SkillEvaluation.objects.get().date, session.date)

        self.assertEqual(self.client.get(url).status_code, 200)

        # Désélectionner un statut supprime la présence
        self.client.post(url, {'_action': 'attendance', f'att_{self.alice.pk}': 'present'})
        self.assertFalse(Attendance.objects.filter(member=self.bob).exists())

    def test_overview_and_student_pages(self):
        session = self._create_session()
        Attendance.objects.create(session=session, member=self.alice, status='present')
        session.skills.add(self.skill_a)

        resp = self.client.get(reverse('suivi-attendance'))
        self.assertContains(resp, 'Martin Alice')
        self.assertContains(resp, '100 %')
        resp = self.client.get(reverse('suivi-students'))
        self.assertContains(resp, 'Martin Alice')
        self.assertEqual(self.client.get(reverse('suivi-sessions')).status_code, 200)

        resp = self.client.get(reverse('suivi-student-detail', args=[self.alice.pk]))
        self.assertContains(resp, '1 séance')

    def test_exercises_and_notes(self):
        url = reverse('suivi-student-detail', args=[self.alice.pk])
        self.client.post(url, {
            '_action': 'add_exercise', 'date': '2026-10-07', 'name': 'Remontée 6 m',
            'skill': self.skill_a.pk, 'result': 'partial', 'comment': '',
        })
        self.assertEqual(Exercise.objects.get().author, self.instructor)
        self.client.post(url, {'_action': 'add_note', 'date': '2026-10-07', 'content': 'Privé', 'visibility': 'staff'})
        self.client.post(url, {'_action': 'add_note', 'date': '2026-10-07', 'content': 'Bravo !', 'visibility': 'student'})
        self.assertEqual(StudentNote.objects.count(), 2)
        resp = self.client.get(url)
        self.assertContains(resp, 'Remontée 6 m')
        self.assertContains(resp, 'Privé')

        # L'élève ne voit que les notes qui lui sont destinées
        self.client.force_login(self.student_user)
        resp = self.client.get(reverse('suivi-mine'))
        self.assertContains(resp, 'Bravo !')
        self.assertContains(resp, 'Remontée 6 m')
        self.assertNotContains(resp, 'Privé')

        note = StudentNote.objects.get(content='Privé')
        self.client.force_login(self.instructor)
        self.client.post(url, {'_action': 'toggle_note', 'note_pk': note.pk})
        note.refresh_from_db()
        self.assertEqual(note.visibility, 'student')

    def test_students_cannot_access_staff_pages(self):
        session = self._create_session()
        self.client.force_login(self.student_user)
        for name, args in [
            ('suivi-sessions', []), ('suivi-attendance', []), ('suivi-students', []),
            ('suivi-session-detail', [session.pk]), ('suivi-student-detail', [self.alice.pk]),
        ]:
            self.assertRedirects(self.client.get(reverse(name, args=args)), reverse('home'), fetch_redirect_response=False)
        resp = self.client.post(reverse('suivi-student-detail', args=[self.alice.pk]), {
            '_action': 'add_note', 'date': '2026-10-07', 'content': 'x', 'visibility': 'student',
        })
        self.assertFalse(StudentNote.objects.exists())

    def test_delete_session(self):
        session = self._create_session()
        Attendance.objects.create(session=session, member=self.alice, status='present')
        self.client.post(reverse('suivi-session-delete', args=[session.pk]))
        self.assertFalse(TrainingSession.objects.exists())
        self.assertFalse(Attendance.objects.exists())
