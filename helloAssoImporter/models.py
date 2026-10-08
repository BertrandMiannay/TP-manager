from django.conf import settings
from django.db import models


class Season(models.Model):
    label = models.CharField(max_length=20)
    current = models.BooleanField(default=False)

    def __str__(self):
        return self.label

    class Meta:
        verbose_name = 'Saison'
        verbose_name_plural = 'Saisons'


class MemberShipForm(models.Model):
    form_slug = models.CharField(primary_key=True)
    title = models.CharField()
    form_type = models.CharField()
    description = models.CharField()
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    updated_at = models.DateTimeField()
    created_at = models.DateTimeField()
    season = models.OneToOneField(Season, null=True, blank=True, on_delete=models.SET_NULL)
    field_mapping = models.JSONField(
        default=dict,
        blank=True,
        help_text="Associe les champs standardisés (définis dans helloAssoApi.LEVEL_FIELD_LABELS) aux noms de champs HelloAsso de ce formulaire.",
    )

    def __str__(self):
        return self.form_slug
    

class EventForm(models.Model):
    form_slug = models.CharField(primary_key=True)
    title = models.CharField()
    form_type = models.CharField()
    description = models.CharField()
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    last_registration_updated = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField()
    created_at = models.DateTimeField()

    def __str__(self):
        return self.form_slug


class EventFormOrder(models.Model):
    order_id = models.IntegerField(primary_key=True)
    form = models.ForeignKey(EventForm, on_delete=models.CASCADE)
    payer_email = models.CharField(null=True)
    payer_first_name = models.CharField(null=True)
    payer_last_name = models.CharField(null=True)
    created_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(null=True)

    def __str__(self):
        return str(self.order_id)


class EventRegistration(models.Model):

    class State(models.TextChoices):
        WAITING = 'Waiting', 'En attente'
        PROCESSED = 'Processed', 'Traité'
        REGISTERED = 'Registered', 'Inscrit'
        DELETED = 'Deleted', 'Supprimé'
        REFUNDED = 'Refunded', 'Remboursé'
        CANCELED = 'Canceled', 'Annulé'
        REFUSED = 'Refused', 'Refusé'
        CONTESTED = 'Contested', 'Contesté'
        ABANDONED = 'Abandoned', 'Abandonné'
        UNKNOWN = 'Unknown', 'Inconnu'

    item_id = models.IntegerField(primary_key=True)
    order = models.ForeignKey(EventFormOrder, on_delete=models.CASCADE)
    name = models.CharField()
    first_name = models.CharField()
    last_name = models.CharField()
    state = models.CharField(max_length=20, choices=State.choices, default=State.UNKNOWN)

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Member(models.Model):
    email = models.CharField()
    first_name = models.CharField()
    last_name = models.CharField()
    birthdate = models.DateField(null=True, blank=True)
    medical_certificate_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['email', 'first_name', 'last_name'],
                name='unique_member_identity',
            )
        ]

    formations = models.ManyToManyField('Cursus', blank=True, related_name='members')

    def __str__(self):
        return f"{self.first_name} {self.last_name} <{self.email}>"


class MemberShipFormOrder(models.Model):
    item_id = models.IntegerField(primary_key=True)
    order_id = models.IntegerField(db_index=True)
    form = models.ForeignKey(MemberShipForm, on_delete=models.CASCADE)
    member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.SET_NULL)
    payer_email = models.CharField()
    payer_first_name = models.CharField()
    payer_last_name = models.CharField()
    category = models.CharField(null=True, blank=True)
    birthdate = models.DateField(null=True, blank=True)
    licence_number = models.CharField(blank=True, default='')
    sex = models.CharField(null=True, blank=True)
    caci_expiration = models.DateField(null=True, blank=True)
    dive_level = models.CharField(blank=True, default='')
    dive_teaching_level = models.CharField(blank=True, default='')
    apnea_level = models.CharField(blank=True, default='')
    apnea_teaching_level = models.CharField(blank=True, default='')
    underwater_shooting_level = models.CharField(blank=True, default='')
    underwater_shooting_teaching_level = models.CharField(blank=True, default='')
    emergency_contact_name = models.CharField(blank=True, default='')
    emergency_contact_phone = models.CharField(blank=True, default='')
    updated_at = models.DateTimeField()
    created_at = models.DateTimeField()

    def __str__(self):
        return str(self.item_id)


class Cursus(models.Model):
    class Status(models.TextChoices):
        ACTIVE   = 'active',   'Actif'
        ARCHIVED = 'archived', 'Archivé'

    name   = models.CharField(max_length=200)
    date   = models.DateField(help_text="Date de version du cursus")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        verbose_name        = 'Cursus'
        verbose_name_plural = 'Cursus'
        ordering            = ['-date']

    def __str__(self):
        return self.name


class CursusCategory(models.Model):
    cursus = models.ForeignKey(Cursus, on_delete=models.CASCADE, related_name='categories')
    name   = models.CharField(max_length=200)
    order  = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name        = 'Catégorie'
        verbose_name_plural = 'Catégories'
        ordering            = ['order', 'pk']

    def __str__(self):
        return f"{self.cursus.name} — {self.name}"


class Skill(models.Model):
    category = models.ForeignKey(CursusCategory, on_delete=models.CASCADE, related_name='skills')
    name     = models.CharField(max_length=200)
    order    = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name        = 'Compétence'
        verbose_name_plural = 'Compétences'
        ordering            = ['order', 'pk']

    def __str__(self):
        return self.name


class MemberSkill(models.Model):
    class SkillStatus(models.TextChoices):
        NOT_ACQUIRED = 'not_acquired', 'Non acquis'
        IN_PROGRESS  = 'in_progress',  "En cours d'acquisition"
        ACQUIRED     = 'acquired',     'Acquis'

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='member_skills')
    skill  = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name='member_skills')
    status = models.CharField(max_length=20, choices=SkillStatus.choices, default=SkillStatus.NOT_ACQUIRED)

    class Meta:
        verbose_name        = 'Compétence membre'
        verbose_name_plural = 'Compétences membres'
        constraints = [
            models.UniqueConstraint(fields=['member', 'skill'], name='unique_member_skill')
        ]

    def __str__(self):
        return f"{self.member} — {self.skill}"


class SkillEvaluation(models.Model):
    member  = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='skill_evaluations')
    skill   = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name='evaluations')
    date    = models.DateField()
    status  = models.CharField(max_length=20, choices=MemberSkill.SkillStatus.choices)
    comment = models.TextField(blank=True)
    author  = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='+', verbose_name='Évaluateur')

    class Meta:
        verbose_name        = 'Évaluation'
        verbose_name_plural = 'Évaluations'
        ordering            = ['-date']

    def __str__(self):
        return f"{self.member} — {self.skill} ({self.date})"