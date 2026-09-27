"""审批候选角色与既有动作权限保持一致，专门角色审批不得扩大。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/backend'))
from app.services.approvals import approver_roles


class ApprovalPermissions(unittest.TestCase):
    def test_general_approval_includes_configuration_manager(self):
        self.assertEqual(set(approver_roles('APPROVER')),
                         {'APPROVER', 'CONFIGURATION_MANAGER'})

    def test_baseline_requires_configuration_manager(self):
        self.assertEqual(approver_roles('CONFIGURATION_MANAGER'), ['CONFIGURATION_MANAGER'])

    def test_unknown_role_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '审批角色无效'):
            approver_roles('UNKNOWN')


if __name__ == '__main__':
    unittest.main()
