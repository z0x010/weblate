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

"""Test for user handling."""

from django.test import TestCase
from django.test.utils import override_settings
from django.core.urlresolvers import reverse
from django.contrib.auth.models import User
from django.core import mail

from weblate.accounts.models import Profile

from weblate.trans.tests.test_views import ViewTestCase
from weblate.lang.models import Language

CONTACT_DATA = {
    'name': 'Test',
    'email': 'noreply@weblate.org',
    'subject': 'Message from dark side',
    'message': 'Hi\n\nThis app looks really cool!',
}


class ViewTest(TestCase):
    """Test for views."""

    def get_user(self):
        user = User.objects.create_user(
            username='testuser',
            password='testpassword'
        )
        user.first_name = 'First Second'
        user.email = 'noreply@weblate.org'
        user.save()
        Profile.objects.get_or_create(user=user)
        return user

    def test_contact(self):
        """Test for contact form."""
        # Basic get
        response = self.client.get(reverse('contact'))
        self.assertContains(response, 'id="id_message"')

        # Sending message
        response = self.client.post(reverse('contact'), CONTACT_DATA)
        self.assertRedirects(response, reverse('home'))

        # Verify message
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            '[Weblate] Message from dark side'
        )

    @override_settings(
        AUTH_MAX_ATTEMPTS=0,
    )
    def test_contact_rate(self):
        """Test for contact form rate limiting."""
        response = self.client.post(reverse('contact'), CONTACT_DATA)
        self.assertContains(
            response,
            'Too many messages sent, please try again later!'
        )

    @override_settings(OFFER_HOSTING=False)
    def test_hosting_disabled(self):
        """Test for hosting form with disabled hosting"""
        self.get_user()
        self.client.login(username='testuser', password='testpassword')
        response = self.client.get(reverse('hosting'))
        self.assertRedirects(response, reverse('home'))

    @override_settings(OFFER_HOSTING=True)
    def test_hosting(self):
        """Test for hosting form with enabled hosting."""
        self.get_user()
        self.client.login(username='testuser', password='testpassword')
        response = self.client.get(reverse('hosting'))
        self.assertContains(response, 'id="id_message"')

        # Sending message
        response = self.client.post(
            reverse('hosting'),
            {
                'name': 'Test',
                'email': 'noreply@weblate.org',
                'project': 'HOST',
                'url': 'http://example.net',
                'repo': 'https://github.com/WeblateOrg/weblate.git',
                'mask': 'po/*.po',
                'message': 'Hi\n\nI want to use it!',
            }
        )
        self.assertRedirects(response, reverse('home'))

        # Verify message
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            '[Weblate] Hosting request for HOST'
        )
        self.assertIn(
            'testuser',
            mail.outbox[0].body,
        )

    def test_contact_subject(self):
        # With set subject
        response = self.client.get(
            reverse('contact'),
            {'t': 'reg'}
        )
        self.assertContains(response, 'Registration problems')

    def test_contact_user(self):
        self.get_user()
        # Login
        self.client.login(username='testuser', password='testpassword')
        response = self.client.get(
            reverse('contact'),
        )
        self.assertContains(response, 'value="First Second"')
        self.assertContains(response, 'noreply@weblate.org')

    def test_user(self):
        """Test user pages."""
        # Setup user
        user = self.get_user()

        # Login as user
        self.client.login(username='testuser', password='testpassword')

        # Get public profile
        response = self.client.get(
            reverse('user_page', kwargs={'user': user.username})
        )
        self.assertContains(response, '="/activity/')

    def test_login(self):
        self.get_user()

        # Login
        response = self.client.post(
            reverse('login'),
            {'username': 'testuser', 'password': 'testpassword'}
        )
        self.assertRedirects(response, reverse('home'))

        # Login redirect
        response = self.client.get(reverse('login'))
        self.assertRedirects(response, reverse('profile'))

        # Logout with GET should fail
        response = self.client.get(reverse('logout'))
        self.assertEqual(response.status_code, 405)

        # Logout
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('home'))

    def test_password(self):
        # Create user
        self.get_user()
        # Login
        self.client.login(username='testuser', password='testpassword')
        # Change without data
        response = self.client.post(
            reverse('password')
        )
        self.assertContains(response, 'This field is required.')
        response = self.client.get(
            reverse('password'),
        )
        self.assertContains(response, 'Current password')
        # Change with wrong password
        response = self.client.post(
            reverse('password'),
            {
                'password': '123456',
                'new_password1': '123456',
                'new_password2': '123456'
            }
        )
        self.assertContains(response, 'You have entered an invalid password.')
        # Change
        response = self.client.post(
            reverse('password'),
            {
                'password': 'testpassword',
                'new_password1': '1pa$$word!',
                'new_password2': '1pa$$word!'
            }
        )

        self.assertRedirects(response, reverse('profile') + '#auth')
        self.assertTrue(
            User.objects.get(username='testuser').check_password('1pa$$word!')
        )

    def test_api_key(self):
        # Create user
        self.get_user()
        # Login
        self.client.login(username='testuser', password='testpassword')

        # API key reset with GET should fail
        response = self.client.get(reverse('reset-api-key'))
        self.assertEqual(response.status_code, 405)

        # API key reset
        response = self.client.post(reverse('reset-api-key'))
        self.assertRedirects(response, reverse('profile') + '#api')


class ProfileTest(ViewTestCase):
    def test_profile(self):
        # Get profile page
        response = self.client.get(reverse('profile'))
        self.assertContains(response, 'action="/accounts/profile/"')
        self.assertContains(response, 'name="secondary_languages"')

        # Save profile
        response = self.client.post(
            reverse('profile'),
            {
                'language': 'cs',
                'languages': Language.objects.get(code='cs').id,
                'secondary_languages': Language.objects.get(code='cs').id,
                'first_name': 'First Last',
                'email': 'noreply@weblate.org',
                'username': 'testik',
                'dashboard_view': Profile.DASHBOARD_WATCHED,
            }
        )
        self.assertRedirects(response, reverse('profile'))
