import logging
import uuid
from datetime import timedelta
from functools import wraps

from django.contrib.auth import get_user_model, login, update_session_auth_hash
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import Group
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import ListView, UpdateView, FormView, View, TemplateView
from django.urls import reverse, reverse_lazy
from django import forms

logger = logging.getLogger(__name__)

INVITE_EXPIRY_DAYS = 7


def _is_club_staff(user):
    return user.is_club_staff


def _make_permission_decorator(check_fn):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('account_login')
            if not check_fn(request.user):
                return redirect('home')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def _is_admin(user):
    return user.is_administrator or user.is_superuser


admin_required = _make_permission_decorator(_is_admin)
club_staff_required = _make_permission_decorator(_is_club_staff)

User = get_user_model()


def _check_passwords_match(cleaned_data, field1, field2):
    p1 = cleaned_data.get(field1)
    p2 = cleaned_data.get(field2)
    if p1 and p2 and p1 != p2:
        raise forms.ValidationError('Les mots de passe ne correspondent pas.')


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = 'home.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        is_admin = self.request.user.is_administrator
        ctx['apps'] = [

            {
                'title': 'Utilisateurs',
                'description': 'Gérer les comptes, les rôles et les invitations.',
                'url': '/users/',
                'visible': is_admin,
            },
            {
                'title': 'Inscriptions',
                'description': "Consulter les formulaires d'adhésion et les membres importés depuis HelloAsso.",
                'url': '/inscriptions/',
                'visible': True,
            },
            {
                'title': 'Suivi des élèves',
                'description': 'Séances hebdomadaires : appel, compétences travaillées, exercices et notes.',
                'url': '/suivi/',
                'visible': self.request.user.is_club_staff,
            },
            {
                'title': 'Mon suivi',
                'description': 'Mes présences, compétences, exercices et remarques des encadrants.',
                'url': '/suivi/mon-suivi/',
                'visible': True,
            },
        ]
        return ctx


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Allow access only to users in the 'admin' group."""

    def test_func(self):
        return self.request.user.is_administrator or self.request.user.is_superuser


class ClubStaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Allow access to instructors, dive directors and admins."""

    def test_func(self):
        return _is_club_staff(self.request.user)


class UserRoleForm(forms.Form):
    role = forms.ChoiceField(choices=[
        ('member', 'Membre'),
        ('instructor', 'Formateur'),
        ('dive_director', 'Directeur de plongée'),
        ('admin', 'Administrateur'),
    ])


class InvitationForm(forms.Form):
    email = forms.EmailField(label='Adresse email')


class AcceptInviteForm(forms.Form):
    username = forms.CharField(label="Nom d'utilisateur")
    password1 = forms.CharField(label='Mot de passe', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Confirmer le mot de passe', widget=forms.PasswordInput)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_username(self):
        username = self.cleaned_data['username']
        qs = User.objects.filter(username=username)
        if self.user:
            qs = qs.exclude(pk=self.user.pk)
        if qs.exists():
            raise forms.ValidationError("Ce nom d'utilisateur est déjà pris.")
        return username

    def clean(self):
        cleaned_data = super().clean()
        _check_passwords_match(cleaned_data, 'password1', 'password2')
        return cleaned_data


class UserListView(AdminRequiredMixin, ListView):
    model = User
    template_name = 'userManagement/user_list.html'
    context_object_name = 'users'

    def get_queryset(self):
        return User.objects.prefetch_related('groups').order_by('username')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['users_with_urls'] = [
            (u, self.request.build_absolute_uri(
                reverse('userManagement:accept_invite', args=[u.invite_token])
            ) if u.invite_token else None)
            for u in ctx['users']
        ]
        return ctx


class UserRoleUpdateView(AdminRequiredMixin, FormView):
    template_name = 'userManagement/user_role_form.html'
    form_class = UserRoleForm
    success_url = reverse_lazy('userManagement:user_list')

    def get_target_user(self):
        return get_object_or_404(User, pk=self.kwargs['pk'])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['target_user'] = self.get_target_user()
        return ctx

    def form_valid(self, form):
        user = self.get_target_user()
        old_groups = list(user.groups.values_list('name', flat=True))
        new_role = form.cleaned_data['role']
        group = Group.objects.get(name=new_role)
        user.groups.set([group])
        logger.info("ROLE_CHANGE user=%s old=%s new=%s by=%s",
                    user.username, old_groups, new_role, self.request.user.username)
        return super().form_valid(form)


class InviteView(AdminRequiredMixin, FormView):
    template_name = 'userManagement/invite.html'
    form_class = InvitationForm

    def form_valid(self, form):
        email = form.cleaned_data['email']

        if User.objects.filter(email=email).exists():
            form.add_error('email', 'Un utilisateur avec cet email existe déjà.')
            return self.form_invalid(form)

        token = uuid.uuid4()
        invite_url = self.request.build_absolute_uri(
            reverse('userManagement:accept_invite', args=[token])
        )
        User.objects.create(
            username=str(token),
            email=email,
            is_active=False,
            invite_token=token,
            invite_expires_at=timezone.now() + timedelta(days=INVITE_EXPIRY_DAYS),
        )
        logger.info("INVITE_SENT to=%s by=%s expires_in=%dd",
                    email, self.request.user.username, INVITE_EXPIRY_DAYS)
        return self.render_to_response(self.get_context_data(
            form=InvitationForm(),
            invitation_url=invite_url,
            invited_email=email,
        ))


class AcceptInviteView(View):
    template_name = 'userManagement/accept_invite.html'

    def get_pending_user(self, token):
        user = get_object_or_404(User, invite_token=token, is_active=False)
        if user.invite_expires_at and user.invite_expires_at < timezone.now():
            raise Http404
        return user

    def get(self, request, token):
        user = self.get_pending_user(token)
        form = AcceptInviteForm(user=user)
        return render(request, self.template_name, {'form': form, 'email': user.email})

    def post(self, request, token):
        user = self.get_pending_user(token)
        form = AcceptInviteForm(request.POST, user=user)
        if form.is_valid():
            user.username = form.cleaned_data['username']
            user.set_password(form.cleaned_data['password1'])
            user.is_active = True
            user.invite_token = None
            user.invite_expires_at = None
            user.save()
            logger.info("INVITE_ACCEPTED user=%s email=%s", user.username, user.email)
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            return redirect('/')
        return render(request, self.template_name, {'form': form, 'email': user.email})



class UserDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        if user != request.user:
            logger.info("USER_DELETE user=%s email=%s by=%s",
                        user.username, user.email, request.user.username)
            user.delete()
        return redirect('userManagement:user_list')


class ChangePasswordForm(forms.Form):
    old_password = forms.CharField(label='Ancien mot de passe', widget=forms.PasswordInput)
    new_password1 = forms.CharField(label='Nouveau mot de passe', widget=forms.PasswordInput)
    new_password2 = forms.CharField(label='Répéter le nouveau mot de passe', widget=forms.PasswordInput)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_old_password(self):
        old_password = self.cleaned_data['old_password']
        if not self.user.check_password(old_password):
            raise forms.ValidationError("Mot de passe incorrect.")
        return old_password

    def clean(self):
        cleaned_data = super().clean()
        _check_passwords_match(cleaned_data, 'new_password1', 'new_password2')
        return cleaned_data


class ChangePasswordView(LoginRequiredMixin, FormView):
    template_name = 'userManagement/change_password.html'
    form_class = ChangePasswordForm
    success_url = reverse_lazy('userManagement:change_password')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        self.request.user.set_password(form.cleaned_data['new_password1'])
        self.request.user.save()
        update_session_auth_hash(self.request, self.request.user)
        messages.success(self.request, 'Mot de passe mis à jour avec succès.')
        return super().form_valid(form)
