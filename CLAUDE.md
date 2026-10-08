# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django 5.2 web application that imports and manages sports club (plongée) membership data and events from the [HelloAsso](https://www.helloasso.com/) platform. Managed with Poetry (Python 3.13).

## Commands

```bash
# Install dependencies
poetry install

# Run database migrations
python manage.py migrate

# Start development server
python manage.py runserver

# Reset dev database (deletes db.sqlite3, re-migrates, creates admin/admin superuser)
python manage.py init_dev_db

# Create migrations after model changes
python manage.py makemigrations helloAssoImporter
python manage.py makemigrations userManagement
python manage.py makemigrations suivi

# Revert all migrations for an app
python manage.py migrate helloAssoImporter zero

# Run tests
python manage.py test

# Run tests for a single app
python manage.py test helloAssoImporter
```

## Environment Variables

A `.env` file is required at the project root with:
```
HELLO_ASSO_API_CLIENT_ID=...
HELLO_ASSO_API_CLIENT_SECRET=...
ORGANIZATION_SLUG=...
```

These are loaded by `common/api/helloAssoApi.py` via `python-dotenv`.

## Architecture

### Django Apps

- **`helloAssoImporter/`** — Core data import app. Models: `MemberShipForm` → `MemberShipFormOrder` → `Member` (cascade) and `EventForm` → `EventFormOrder` → `EventRegistration` (cascade). All registered in Django admin.
- **`userManagement/`** — Custom user model (`CustomUser` extends `AbstractUser`), group-based role system, invitation-only registration flow. `CustomUser` is registered in Django admin via `CustomUserAdmin`.
- **`suivi/`** — Suivi des élèves. Models: `TrainingSession` (séance hebdomadaire : date, saison, cursus préparés, compétences travaillées, encadrants), `Attendance` (présent / absent / excusé, unique par séance × membre), `Exercise` (exercice réalisé par un élève, lié optionnellement à une séance et une compétence), `StudentNote` (note avec visibilité `staff` = équipe encadrante uniquement, ou `student` = visible par l'élève). Les élèves sont les `Member` de la saison en cours ayant au moins une formation (`Member.formations`). L'évaluation par séance met à jour `MemberSkill` et historise dans `SkillEvaluation` (date = date de la séance). L'espace élève (`/suivi/mon-suivi/`) retrouve les fiches par email (`Member.email` == `user.email`) et n'affiche que les notes `student`.
- **`common/api/helloAssoApi.py`** — Shared `HelloAssoApi` class wrapping the `helloasso-python` SDK (OpenAPI/Pydantic). Handles token auth, automatic token refresh on 401, and all API calls to HelloAsso.
- **`config/`** — Django project settings, root URL conf, ASGI/WSGI.
- **`tpmanagement/`** — Stub directory, currently unused.

### Role System

Group-based via Django Groups. The `_create_default_groups` signal (post_migrate) creates them automatically.

| Groupe | Label affiché | Accès |
|--------|---------------|-------|
| `admin` | Administrateur | Gestion utilisateurs + sorties |
| `member` | Membre | Sorties uniquement |
| `instructor` | Formateur | Sorties uniquement |
| `dive_director` | Directeur de plongée | Sorties uniquement |

- `AdminRequiredMixin` et `CustomUser.is_administrator` vérifient le groupe `admin`.
- `manager` et `viewer` sont obsolètes — remplacés par les rôles ci-dessus.

### Data Import Flow

`HelloAssoApi` drives the import cascade for events:
1. `refresh_event_forms()` — fetches Event forms from HelloAsso, saves `EventForm` records.
2. `get_event_form_orders(form, since=...)` — fetches orders for a form (filtered by `last_registration_updated`), saves `EventFormOrder` and `EventRegistration` records.

The global refresh (bouton "Rafraîchir" sur la page Sorties) runs both steps for all forms and displays a success notification with counts and duration.

Membership import also exists (`refresh_membership_forms`, `get_member_registry`) but is not exposed in the UI.

### HelloAsso API Client

- Uses `helloasso-python` SDK (OpenAPI auto-generated, Pydantic models).
- Token fetched via `client_credentials` OAuth2 flow.
- All SDK calls go through `_call(fn, *args, **kwargs)` which catches `ApiException(401)`, reauthenticates once automatically, and retries. On second failure, raises `HelloAssoApiError` with a clear message.
- Instance is cached as a singleton (`_hello_asso_api_instance`), initialized at startup via `init_hello_asso_api()`.
- **Creating events** via `create_event_form()` requires the `FormAdministration` privilege on the API client (not yet activated). The UI button is present but affiche "WIP" en attendant.

### Authentication & Authorization

- `django-allauth` handles login/signup; signup is invitation-only (`INVITATIONS_INVITATION_ONLY = True`).
- `AdminRequiredMixin` restricts views to users in the Django `admin` group.
- All views require login. The home page and navbar show/hide sections based on `user.is_administrator`.
- Email backend is set to console for development.

### URL Structure

| Prefix | App |
|---|---|
| `/` | Home (`userManagement.HomeView`) |
| `/admin/` | Django admin |
| `/accounts/` | allauth auth |
| `/invitations/` | django-invitations |
| `/inscriptions/` | helloAssoImporter (liste des sorties, refresh, création WIP) |
| `/suivi/` | suivi (séances, présences, élèves — staff ; `mon-suivi/` — tout utilisateur connecté) |
| `/users/` | userManagement |
