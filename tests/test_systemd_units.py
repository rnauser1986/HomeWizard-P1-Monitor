"""The unit files in install/: both services carry the same hardening, and the lines that the programs need stay."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNITS = ("homewizard-api.service", "homewizard-collector.service")

HARDENING = (
    "NoNewPrivileges=yes", "PrivateTmp=yes", "ProtectSystem=strict", "ReadWritePaths=/opt/homewizard", "ProtectHome=yes",
    "ProtectKernelTunables=yes", "ProtectKernelModules=yes", "ProtectKernelLogs=yes", "ProtectControlGroups=yes",
    "ProtectClock=yes", "ProtectHostname=yes", "ProtectProc=invisible", "ProcSubset=pid", "PrivateDevices=yes",
    "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX", "RestrictNamespaces=yes", "RestrictSUIDSGID=yes",
    "RestrictRealtime=yes", "LockPersonality=yes", "SystemCallArchitectures=native", "CapabilityBoundingSet=",
    "SystemCallFilter=@system-service", "SystemCallErrorNumber=EPERM", "UMask=0077",
)


def lines(name):
    with open(os.path.join(ROOT, "install", name), encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]


class UnitFileTests(unittest.TestCase):
    def test_every_service_has_the_hardening(self):
        for name in UNITS:
            have = lines(name)
            for want in HARDENING:
                self.assertIn(want, have, f"{name} lacks {want}")

    def test_the_two_services_share_the_same_hardening_lines(self):
        keys = {ln.split("=")[0] for ln in HARDENING}
        sets = [{ln for ln in lines(n) if ln.split("=")[0] in keys} for n in UNITS]
        self.assertEqual(sets[0], sets[1])

    def test_no_service_gets_a_capability_back(self):
        for name in UNITS:
            have = lines(name)
            self.assertIn("CapabilityBoundingSet=", have)
            self.assertEqual([ln for ln in have if ln.startswith("AmbientCapabilities=")], [])

    def test_the_program_still_starts_from_its_folder(self):
        for name in UNITS:
            have = lines(name)
            self.assertIn("WorkingDirectory=/opt/homewizard", have)
            self.assertTrue(any(ln.startswith("ExecStart=/usr/bin/python3 ") for ln in have))
            self.assertIn("Restart=always", have)


if __name__ == "__main__":
    unittest.main()
