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

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin, GroupAdmin
from django.contrib.auth.forms import UserCreationForm, UserChangeForm

from weblate.accounts.forms import UniqueEmailMixin


class ProfileAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'full_name', 'language', 'suggested', 'translated'
    ]
    search_fields = [
        'user__username', 'user__email', 'user__first_name'
    ]
    list_filter = ['language']


class VerifiedEmailAdmin(admin.ModelAdmin):
    list_display = ('social', 'email')
    search_fields = (
        'email', 'social__user__username', 'social__user__email'
    )
    raw_id_fields = ('social',)


class WeblateUserChangeForm(UserChangeForm):
    def __init__(self, *args, **kwargs):
        super(WeblateUserChangeForm, self).__init__(*args, **kwargs)
        self.fields['email'].required = True


class WeblateUserCreationForm(UserCreationForm, UniqueEmailMixin):
    validate_unique_mail = True

    class Meta(object):
        fields = ('username', 'email')

    def __init__(self, *args, **kwargs):
        super(WeblateUserCreationForm, self).__init__(*args, **kwargs)
        self.fields['email'].required = True


class WeblateUserAdmin(UserAdmin):
    """Custom UserAdmin class.

    Used to add listing of group membership and whether user is active.
    """
    list_display = UserAdmin.list_display + ('is_active', 'user_groups', 'id')
    form = WeblateUserChangeForm
    add_form = WeblateUserCreationForm
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'password1', 'password2'),
        }),
    )

    def user_groups(self, obj):
        """Display comma separated list of user groups."""
        return ','.join([g.name for g in obj.groups.all()])


class WeblateGroupAdmin(GroupAdmin):
    save_as = True
