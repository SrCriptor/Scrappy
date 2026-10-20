import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import admin_store
from admin_store import _development_owner_password_matches


class DevelopmentOwnerPasswordTests(unittest.TestCase):
    def test_allows_secret_only_in_replit_development(self):
        with patch.dict(
            os.environ,
            {
                'REPLIT_DEV_DOMAIN': 'workspace.replit.dev',
                'DEV_ADMIN_PASSWORD': 'test-only-password',
            },
            clear=True,
        ):
            self.assertTrue(
                _development_owner_password_matches('owner', 'test-only-password')
            )

    def test_rejects_other_usernames_and_wrong_passwords(self):
        with patch.dict(
            os.environ,
            {
                'REPLIT_DEV_DOMAIN': 'workspace.replit.dev',
                'DEV_ADMIN_PASSWORD': 'test-only-password',
            },
            clear=True,
        ):
            self.assertFalse(
                _development_owner_password_matches('admin', 'test-only-password')
            )
            self.assertFalse(
                _development_owner_password_matches('owner', 'wrong-password')
            )

    def test_rejects_if_render_variable_exists_even_empty(self):
        with patch.dict(
            os.environ,
            {
                'REPLIT_DEV_DOMAIN': 'workspace.replit.dev',
                'DEV_ADMIN_PASSWORD': 'test-only-password',
                'RENDER': '',
            },
            clear=True,
        ):
            self.assertFalse(
                _development_owner_password_matches('owner', 'test-only-password')
            )

    def test_rejects_production_or_missing_replit_dev_domain(self):
        with patch.dict(
            os.environ,
            {
                'REPLIT_DEV_DOMAIN': 'workspace.replit.dev',
                'DEV_ADMIN_PASSWORD': 'test-only-password',
                'APP_ENV': 'production',
            },
            clear=True,
        ):
            self.assertFalse(
                _development_owner_password_matches('owner', 'test-only-password')
            )

        with patch.dict(
            os.environ,
            {'DEV_ADMIN_PASSWORD': 'test-only-password'},
            clear=True,
        ):
            self.assertFalse(
                _development_owner_password_matches('owner', 'test-only-password')
            )

    def test_rejects_when_secret_is_missing(self):
        with patch.dict(
            os.environ,
            {'REPLIT_DEV_DOMAIN': 'workspace.replit.dev'},
            clear=True,
        ):
            self.assertFalse(
                _development_owner_password_matches('owner', 'test-only-password')
            )

    def test_authenticate_accepts_secret_only_after_loading_owner_from_store(self):
        class FakeConnection:
            def execute(self, *_args):
                pass

            def fetchone(self):
                return {
                    'id': 1,
                    'username': 'owner',
                    'password': 'stored-hash',
                    'role': 'owner',
                    'active': True,
                }

        @contextmanager
        def fake_connection():
            yield FakeConnection()

        with (
            patch.dict(
                os.environ,
                {
                    'REPLIT_DEV_DOMAIN': 'workspace.replit.dev',
                    'DEV_ADMIN_PASSWORD': 'test-only-password',
                },
                clear=True,
            ),
            patch.object(admin_store, '_conn', fake_connection),
            patch.object(admin_store, 'check_password_hash', return_value=False),
        ):
            user = admin_store.authenticate('owner', 'test-only-password')

        self.assertEqual(user['id'], 1)
        self.assertEqual(user['role'], 'owner')

    def test_authenticate_does_not_use_override_when_render_is_present(self):
        class FakeConnection:
            def execute(self, *_args):
                pass

            def fetchone(self):
                return {
                    'id': 1,
                    'username': 'owner',
                    'password': 'stored-hash',
                    'role': 'owner',
                    'active': True,
                }

        @contextmanager
        def fake_connection():
            yield FakeConnection()

        with (
            patch.dict(
                os.environ,
                {
                    'REPLIT_DEV_DOMAIN': 'workspace.replit.dev',
                    'DEV_ADMIN_PASSWORD': 'test-only-password',
                    'RENDER': '',
                },
                clear=True,
            ),
            patch.object(admin_store, '_conn', fake_connection),
            patch.object(admin_store, 'check_password_hash', return_value=False),
        ):
            user = admin_store.authenticate('owner', 'test-only-password')

        self.assertIsNone(user)


if __name__ == '__main__':
    unittest.main()
