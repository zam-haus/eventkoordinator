"""
Management command to promote an existing user to admin (staff + superuser).

Usage:
    python manage.py promote_admin --username user123
"""

from django.core.management.base import BaseCommand, CommandError
from openid_user_management.models import OpenIDUser


class Command(BaseCommand):
    help = 'Promote an existing user to admin (is_staff=True, is_superuser=True)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            required=True,
            help='Username of the user to promote'
        )
        parser.add_argument(
            '--revoke',
            action='store_true',
            help='Revoke admin rights instead of granting them'
        )

    def handle(self, *args, **options):
        username = options['username']
        revoke = options['revoke']

        try:
            user = OpenIDUser.objects.get(username=username)
        except OpenIDUser.DoesNotExist:
            raise CommandError(f'User with username "{username}" does not exist')

        user.is_staff = not revoke
        user.is_superuser = not revoke
        user.save(update_fields=['is_staff', 'is_superuser'])

        action = 'Revoked admin rights from' if revoke else 'Promoted'
        self.stdout.write(
            self.style.SUCCESS(f'{action} user "{user.username}" ({user.email})')
        )
        self.stdout.write(f'  Staff: {user.is_staff}')
        self.stdout.write(f'  Superuser: {user.is_superuser}')
