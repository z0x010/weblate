# -*- coding: utf-8 -*-
#
# Copyright © 2012 - 2017 Michal Čihař <michal@cihar.com>
#
# This file is part of Weblate <https://weblate.org/>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from __future__ import unicode_literals

from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, HttpResponseRedirect
from django.contrib.auth import logout
from django.conf import settings
from django.middleware.csrf import rotate_token
from django.utils.translation import ugettext as _
from django.contrib.auth.decorators import login_required
from django.core.mail.message import EmailMultiAlternatives
from django.utils import translation
from django.utils.cache import patch_response_headers
from django.utils.crypto import get_random_string
from django.utils.translation import get_language
from django.contrib.auth.models import User
from django.contrib.auth import views as auth_views
from django.views.generic import TemplateView, ListView
from django.views.decorators.http import require_POST
from django.contrib.auth import update_session_auth_hash
from django.core.urlresolvers import reverse
from django.utils.http import urlencode

from rest_framework.authtoken.models import Token

from social_core.backends.utils import load_backends
from social_django.utils import BACKENDS
from social_django.views import complete

from weblate.accounts.forms import (
    RegistrationForm, PasswordChangeForm, EmailForm, ResetForm,
    LoginForm, HostingForm, CaptchaForm, SetPasswordForm,
)
from weblate.accounts.ratelimit import check_rate_limit
from weblate.logger import LOGGER
from weblate.accounts.avatar import get_avatar_image, get_fallback_avatar_url
from weblate.accounts.models import set_lang, remove_user, Profile
from weblate.utils import messages
from weblate.trans.models import Change, Project, SubProject, Suggestion
from weblate.trans.views.helper import get_project
from weblate.accounts.forms import (
    ProfileForm, SubscriptionForm, UserForm, ContactForm,
    SubscriptionSettingsForm, UserSettingsForm, DashboardSettingsForm
)
from weblate.accounts.notifications import notify_account_activity

CONTACT_TEMPLATE = '''
Message from %(name)s <%(email)s>:

%(message)s
'''

HOSTING_TEMPLATE = '''
%(name)s <%(email)s> wants to host %(project)s

Project:    %(project)s
Website:    %(url)s
Repository: %(repo)s
Filemask:   %(mask)s
Username:   %(username)s

Additional message:

%(message)s
'''

CONTACT_SUBJECTS = {
    'lang': 'New language request',
    'reg': 'Registration problems',
    'hosting': 'Commercial hosting',
    'account': 'Suspicious account activity',
}


class RegistrationTemplateView(TemplateView):
    """Class for rendering registration pages."""
    def get_context_data(self, **kwargs):
        """Create context for rendering page."""
        context = super(RegistrationTemplateView, self).get_context_data(
            **kwargs
        )
        context['title'] = _('User registration')
        return context

    def get(self, request, *args, **kwargs):
        if not request.session.get('registration-email-sent'):
            return redirect('home')

        # Remove session for not authenticated user here.
        # It is no longer needed and will just cause problems
        # with multiple registrations from single browser.
        if not request.user.is_authenticated:
            request.session.flush()
        else:
            request.session.pop('registration-email-sent')

        return super(RegistrationTemplateView, self).get(
            request, *args, **kwargs
        )


def mail_admins_contact(request, subject, message, context, sender):
    """Send a message to the admins, as defined by the ADMINS setting."""
    LOGGER.info(
        'contact form from %s',
        sender,
    )
    if not settings.ADMINS:
        messages.error(
            request,
            _('Message could not be sent to administrator!')
        )
        LOGGER.error(
            'ADMINS not configured, can not send message!'
        )
        return

    mail = EmailMultiAlternatives(
        '{0}{1}'.format(settings.EMAIL_SUBJECT_PREFIX, subject % context),
        message % context,
        to=[a[1] for a in settings.ADMINS],
        headers={'Reply-To': sender},
    )

    mail.send(fail_silently=False)

    messages.success(
        request,
        _('Message has been sent to administrator.')
    )


def deny_demo(request):
    """Deny editing of demo account on demo server."""
    messages.warning(
        request,
        _('You cannot change demo account on the demo server.')
    )
    return redirect_profile(request.POST.get('activetab'))


def redirect_profile(page=''):
    url = reverse('profile')
    if page and page.startswith('#'):
        url = url + page
    return HttpResponseRedirect(url)


@login_required
def user_profile(request):

    profile = request.user.profile

    if not profile.language:
        profile.language = get_language()
        profile.save()

    form_classes = [
        ProfileForm,
        SubscriptionForm,
        SubscriptionSettingsForm,
        UserSettingsForm,
        DashboardSettingsForm,
    ]

    if request.method == 'POST':
        # Parse POST params
        forms = [form(request.POST, instance=profile) for form in form_classes]
        forms.append(UserForm(request.POST, instance=request.user))

        if settings.DEMO_SERVER and request.user.username == 'demo':
            return deny_demo(request)

        if all(form.is_valid() for form in forms):
            # Save changes
            for form in forms:
                form.save()

            # Change language
            set_lang(request, request.user.profile)

            # Redirect after saving (and possibly changing language)
            response = redirect_profile(request.POST.get('activetab'))

            # Set language cookie and activate new language (for message below)
            lang_code = profile.language
            response.set_cookie(settings.LANGUAGE_COOKIE_NAME, lang_code)
            translation.activate(lang_code)

            messages.success(request, _('Your profile has been updated.'))

            return response
    else:
        forms = [form(instance=profile) for form in form_classes]
        forms.append(UserForm(instance=request.user))

    social = request.user.social_auth.all()
    social_names = [assoc.provider for assoc in social]
    all_backends = set(load_backends(BACKENDS).keys())
    new_backends = [
        x for x in all_backends
        if x == 'email' or x not in social_names
    ]
    license_projects = SubProject.objects.filter(
        project__in=Project.objects.all_acl(request.user)
    ).exclude(
        license=''
    )

    result = render(
        request,
        'accounts/profile.html',
        {
            'form': forms[0],
            'subscriptionform': forms[1],
            'subscriptionsettingsform': forms[2],
            'usersettingsform': forms[3],
            'dashboardsettingsform': forms[4],
            'userform': forms[5],
            'profile': profile,
            'title': _('User profile'),
            'licenses': license_projects,
            'associated': social,
            'new_backends': new_backends,
            'managed_projects': Project.objects.filter(
                groupacl__groups__name__endswith='@Administration',
                groupacl__groups__user=request.user,
            ).distinct(),
        }
    )
    result.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        profile.language
    )
    return result


@login_required
def user_remove(request):
    if settings.DEMO_SERVER and request.user.username == 'demo':
        return deny_demo(request)

    if request.method == 'POST':
        remove_user(request.user)

        logout(request)

        messages.success(
            request,
            _('Your account has been removed.')
        )

        return redirect('home')

    return render(
        request,
        'accounts/removal.html',
    )


def get_initial_contact(request):
    """Fill in initial contact form fields from request."""
    initial = {}
    if request.user.is_authenticated:
        initial['name'] = request.user.first_name
        initial['email'] = request.user.email
    return initial


def contact(request):
    if request.method == 'POST':
        form = ContactForm(request.POST)
        if not check_rate_limit(request):
            messages.error(
                request,
                _('Too many messages sent, please try again later!')
            )
        elif form.is_valid():
            mail_admins_contact(
                request,
                '%(subject)s',
                CONTACT_TEMPLATE,
                form.cleaned_data,
                form.cleaned_data['email'],
            )
            return redirect('home')
    else:
        initial = get_initial_contact(request)
        if request.GET.get('t') in CONTACT_SUBJECTS:
            initial['subject'] = CONTACT_SUBJECTS[request.GET['t']]
        form = ContactForm(initial=initial)

    return render(
        request,
        'accounts/contact.html',
        {
            'form': form,
            'title': _('Contact'),
        }
    )


@login_required
def hosting(request):
    """Form for hosting request."""
    if not settings.OFFER_HOSTING:
        return redirect('home')

    if request.method == 'POST':
        form = HostingForm(request.POST)
        if form.is_valid():
            context = form.cleaned_data
            context['username'] = request.user.username
            mail_admins_contact(
                request,
                'Hosting request for %(project)s',
                HOSTING_TEMPLATE,
                context,
                form.cleaned_data['email'],
            )
            return redirect('home')
    else:
        initial = get_initial_contact(request)
        form = HostingForm(initial=initial)

    return render(
        request,
        'accounts/hosting.html',
        {
            'form': form,
            'title': _('Hosting'),
        }
    )


def user_page(request, user):
    """User details page."""
    user = get_object_or_404(User, username=user)
    profile = Profile.objects.get_or_create(user=user)[0]

    # Filter all user activity
    all_changes = Change.objects.last_changes(request.user).filter(
        user=user,
    )

    # Last user activity
    last_changes = all_changes[:10]

    # Filter where project is active
    user_projects_ids = set(all_changes.values_list(
        'translation__subproject__project', flat=True
    ))
    user_projects = Project.objects.filter(id__in=user_projects_ids)

    return render(
        request,
        'accounts/user.html',
        {
            'page_profile': profile,
            'page_user': user,
            'last_changes': last_changes,
            'last_changes_url': urlencode(
                {'user': user.username}
            ),
            'user_projects': user_projects,
        }
    )


def user_avatar(request, user, size):
    """User avatar view."""
    user = get_object_or_404(User, username=user)

    if user.email == 'noreply@weblate.org':
        return redirect(get_fallback_avatar_url(size))

    response = HttpResponse(
        content_type='image/png',
        content=get_avatar_image(request, user, size)
    )

    patch_response_headers(response, 3600 * 24 * 7)

    return response


def weblate_login(request):
    """Login handler, just wrapper around standard Django login."""

    # Redirect logged in users to profile
    if request.user.is_authenticated:
        return redirect_profile()

    # Redirect if there is only one backend
    auth_backends = list(load_backends(BACKENDS).keys())
    if len(auth_backends) == 1 and auth_backends[0] != 'email':
        return redirect('social:begin', auth_backends[0])

    return auth_views.login(
        request,
        template_name='accounts/login.html',
        authentication_form=LoginForm,
        extra_context={
            'login_backends': [
                x for x in auth_backends if x != 'email'
            ],
            'can_reset': 'email' in auth_backends,
            'title': _('Login'),
        }
    )


@require_POST
@login_required
def weblate_logout(request):
    """Logout handler, just wrapper around standard Django logout."""
    messages.info(request, _('Thanks for using Weblate!'))

    return auth_views.logout(
        request,
        next_page=reverse('home'),
    )


def register(request):
    """Registration form."""
    captcha_form = None

    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if settings.REGISTRATION_CAPTCHA:
            captcha_form = CaptchaForm(request, request.POST)
        if ((captcha_form is None or captcha_form.is_valid()) and
                form.is_valid() and settings.REGISTRATION_OPEN):
            if form.cleaned_data['email_user']:
                notify_account_activity(
                    form.cleaned_data['email_user'],
                    request,
                    'connect'
                )
                request.session['registration-email-sent'] = True
                return redirect('email-sent')
            return complete(request, 'email')
    else:
        form = RegistrationForm()
        if settings.REGISTRATION_CAPTCHA:
            captcha_form = CaptchaForm(request)

    backends = set(load_backends(BACKENDS).keys())

    # Redirect if there is only one backend
    if len(backends) == 1 and 'email' not in backends:
        return redirect('social:begin', backends.pop())

    return render(
        request,
        'accounts/register.html',
        {
            'registration_email': 'email' in backends,
            'registration_backends': backends - set(['email']),
            'title': _('User registration'),
            'form': form,
            'captcha_form': captcha_form,
        }
    )


@login_required
def email_login(request):
    """Connect email."""
    captcha_form = None

    if request.method == 'POST':
        form = EmailForm(request.POST)
        if settings.REGISTRATION_CAPTCHA:
            captcha_form = CaptchaForm(request, request.POST)
        if ((captcha_form is None or captcha_form.is_valid()) and
                form.is_valid()):
            if form.cleaned_data['email_user']:
                notify_account_activity(
                    form.cleaned_data['email_user'],
                    request,
                    'connect'
                )
                request.session['registration-email-sent'] = True
                return redirect('email-sent')
            return complete(request, 'email')
    else:
        form = EmailForm()
        if settings.REGISTRATION_CAPTCHA:
            captcha_form = CaptchaForm(request)

    return render(
        request,
        'accounts/email.html',
        {
            'title': _('Register email'),
            'form': form,
            'captcha_form': captcha_form,
        }
    )


@login_required
def password(request):
    """Password change / set form."""
    if settings.DEMO_SERVER and request.user.username == 'demo':
        return deny_demo(request)

    do_change = False

    attempts = request.session.get('auth_attempts', 0)

    if not request.user.has_usable_password():
        do_change = True
        change_form = None
    elif request.method == 'POST':
        if attempts >= settings.AUTH_MAX_ATTEMPTS:
            logout(request)
            messages.error(
                request,
                _('Too many authentication attempts!')
            )
            return redirect('login')
        else:
            change_form = PasswordChangeForm(request.POST)
            if change_form.is_valid():
                cur_password = change_form.cleaned_data['password']
                do_change = request.user.check_password(cur_password)
                if not do_change:
                    request.session['auth_attempts'] = attempts + 1
                    messages.error(
                        request,
                        _('You have entered an invalid password.')
                    )
                    rotate_token(request)
                else:
                    request.session['auth_attempts'] = 0

    else:
        change_form = PasswordChangeForm()

    if request.method == 'POST':
        form = SetPasswordForm(request.user, request.POST)
        if form.is_valid() and do_change:

            # Clear flag forcing user to set password
            redirect_page = '#auth'
            if 'show_set_password' in request.session:
                del request.session['show_set_password']
                redirect_page = ''

            # Change the password
            user = form.save()

            # Updating the password logs out all other sessions for the user
            # except the current one.
            update_session_auth_hash(request, user)

            # Change key for current session
            request.session.cycle_key()

            messages.success(
                request,
                _('Your password has been changed.')
            )
            notify_account_activity(request.user, request, 'password')
            return redirect_profile(redirect_page)
    else:
        form = SetPasswordForm(request.user)

    return render(
        request,
        'accounts/password.html',
        {
            'title': _('Change password'),
            'change_form': change_form,
            'form': form,
        }
    )


def reset_password(request):
    """Password reset handling."""
    if 'email' not in load_backends(BACKENDS).keys():
        messages.error(
            request,
            _('Can not reset password, email authentication is disabled!')
        )
        return redirect('login')

    captcha_form = None

    if request.method == 'POST':
        form = ResetForm(request.POST)
        if settings.REGISTRATION_CAPTCHA:
            captcha_form = CaptchaForm(request, request.POST)
        if ((captcha_form is None or captcha_form.is_valid()) and
                form.is_valid()):
            # Force creating new session
            request.session.create()
            if request.user.is_authenticated:
                logout(request)

            if form.cleaned_data['email_user']:
                request.session['password_reset'] = True
                return complete(request, 'email')
            else:
                request.session['registration-email-sent'] = True
                return redirect('email-sent')
    else:
        form = ResetForm()
        if settings.REGISTRATION_CAPTCHA:
            captcha_form = CaptchaForm(request)

    return render(
        request,
        'accounts/reset.html',
        {
            'title': _('Password reset'),
            'form': form,
            'captcha_form': captcha_form,
        }
    )


@require_POST
@login_required
def reset_api_key(request):
    """Reset user API key"""
    if hasattr(request.user, 'auth_token'):
        request.user.auth_token.delete()
    Token.objects.create(
        user=request.user,
        key=get_random_string(40)
    )

    return redirect_profile('#api')


@login_required
def watch(request, project):
    obj = get_project(request, project)
    request.user.profile.subscriptions.add(obj)
    return redirect(obj)


@login_required
def unwatch(request, project):
    obj = get_project(request, project)
    request.user.profile.subscriptions.remove(obj)
    return redirect(obj)


class SuggestionView(ListView):
    paginate_by = 25
    model = Suggestion

    def get_queryset(self):
        return Suggestion.objects.filter(
            user=get_object_or_404(User, username=self.kwargs['user']),
            project__in=Project.objects.all_acl(self.request.user)
        )

    def get_context_data(self):
        result = super(SuggestionView, self).get_context_data()
        user = get_object_or_404(User, username=self.kwargs['user'])
        result['page_user'] = user
        result['page_profile'] = user.profile
        return result
