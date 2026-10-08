from django.conf import settings
from django.db import models

from helloAssoImporter.models import Cursus, Member, MemberSkill, Season, Skill


class TrainingSession(models.Model):
    """Séance hebdomadaire de formation."""

    season      = models.ForeignKey(Season, null=True, blank=True, on_delete=models.SET_NULL, related_name='training_sessions')
    date        = models.DateField()
    title       = models.CharField(max_length=200, blank=True, help_text="Thème de la séance (facultatif)")
    location    = models.CharField(max_length=200, blank=True)
    cursus      = models.ManyToManyField(Cursus, blank=True, related_name='training_sessions',
                                         help_text="Niveaux préparés pendant la séance")
    instructors = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='training_sessions')
    notes       = models.TextField(blank=True, help_text="Déroulé / remarques (visibles par l'équipe encadrante uniquement)")
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'Séance'
        verbose_name_plural = 'Séances'
        ordering            = ['-date', '-pk']

    def __str__(self):
        label = f"Séance du {self.date:%d/%m/%Y}"
        return f"{label} — {self.title}" if self.title else label

    def roster(self):
        """Élèves attendus : adhérents de la saison inscrits à l'un des cursus de la séance,
        plus toute personne ayant déjà une présence enregistrée."""
        cursus_ids = list(self.cursus.values_list('pk', flat=True))
        members = Member.objects.filter(formations__isnull=False)
        if cursus_ids:
            members = members.filter(formations__in=cursus_ids)
        if self.season_id:
            members = members.filter(membershipformorder__form__season_id=self.season_id)
        expected = members.values_list('pk', flat=True)
        recorded = self.attendances.values_list('member_id', flat=True)
        return (
            Member.objects
            .filter(models.Q(pk__in=expected) | models.Q(pk__in=recorded))
            .distinct()
            .order_by('last_name', 'first_name')
        )


class Attendance(models.Model):
    class Status(models.TextChoices):
        PRESENT = 'present', 'Présent'
        ABSENT  = 'absent',  'Absent'
        EXCUSED = 'excused', 'Absent excusé'

    session = models.ForeignKey(TrainingSession, on_delete=models.CASCADE, related_name='attendances')
    member  = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='attendances')
    status  = models.CharField(max_length=20, choices=Status.choices)
    comment = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name        = 'Présence'
        verbose_name_plural = 'Présences'
        constraints = [
            models.UniqueConstraint(fields=['session', 'member'], name='unique_session_attendance'),
        ]

    def __str__(self):
        return f"{self.member} — {self.session} : {self.get_status_display()}"


class WorkedSkill(models.Model):
    """Compétence travaillée par un élève lors d'une séance, avec l'évaluation donnée ce jour-là."""

    session = models.ForeignKey(TrainingSession, on_delete=models.CASCADE, related_name='worked_skills')
    member  = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='worked_skills')
    skill   = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name='worked_skills')
    status  = models.CharField(max_length=20, choices=MemberSkill.SkillStatus.choices, blank=True,
                               help_text="Évaluation lors de la séance (vide = travaillée sans évaluation)")
    comment = models.CharField(max_length=255, blank=True)
    author  = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='+', verbose_name='Saisi par')

    class Meta:
        verbose_name        = 'Compétence travaillée'
        verbose_name_plural = 'Compétences travaillées'
        constraints = [
            models.UniqueConstraint(fields=['session', 'member', 'skill'], name='unique_worked_skill'),
        ]

    def __str__(self):
        return f"{self.member} — {self.skill} ({self.session.date:%d/%m/%Y})"


class Exercise(models.Model):
    """Exercice réalisé par un élève (lors d'une séance ou non)."""

    class Result(models.TextChoices):
        NOT_DONE = 'not_done', 'À refaire'
        PARTIAL  = 'partial',  'Partiellement réussi'
        SUCCESS  = 'success',  'Réussi'

    member      = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='exercises')
    session     = models.ForeignKey(TrainingSession, null=True, blank=True, on_delete=models.SET_NULL, related_name='exercises')
    skill       = models.ForeignKey(Skill, null=True, blank=True, on_delete=models.SET_NULL, related_name='exercises')
    date        = models.DateField()
    name        = models.CharField(max_length=200)
    result      = models.CharField(max_length=20, choices=Result.choices, default=Result.SUCCESS)
    comment     = models.TextField(blank=True)
    author      = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'Exercice'
        verbose_name_plural = 'Exercices'
        ordering            = ['-date', '-pk']

    def __str__(self):
        return f"{self.member} — {self.name} ({self.date:%d/%m/%Y})"


class StudentNote(models.Model):
    class Visibility(models.TextChoices):
        STAFF   = 'staff',   'Équipe encadrante uniquement'
        STUDENT = 'student', "Visible par l'élève"

    member     = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='student_notes')
    session    = models.ForeignKey(TrainingSession, null=True, blank=True, on_delete=models.SET_NULL, related_name='student_notes')
    date       = models.DateField()
    content    = models.TextField()
    visibility = models.CharField(max_length=20, choices=Visibility.choices, default=Visibility.STAFF)
    author     = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'Note élève'
        verbose_name_plural = 'Notes élèves'
        ordering            = ['-date', '-pk']

    def __str__(self):
        return f"{self.member} — {self.date:%d/%m/%Y} ({self.get_visibility_display()})"
