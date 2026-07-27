# ruff: noqa: E501
"""Apply TuneForge's pinned v1.7 cross-vendor canonicalization Wave 2.

Dry-run is the default. The batch is pinned to a verified revision-0019
backup, 11 official manuals, 136 visually reviewed source records, 23 new
canonical identities, and six existing identities. Every mapping requires an
exact normalized label, matching official behavior, and consistent menu and
document context. Supporting standards never replace the official manuals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import func, select, text

from app import config
from app.db.models import (
    CanonicalClassificationEvent,
    CanonicalIdentityEvent,
    CanonicalMappingEvent,
    CanonicalSetting,
    CanonicalSettingAlias,
    CanonicalSettingClassification,
    Manual,
    Setting,
    SettingCanonicalMapping,
    SettingMenuContext,
    SettingMenuContextCitation,
    SettingMenuContextEvent,
)
from app.db.session import SessionLocal
from app.domain import canonical_identity_review, canonical_settings, canonicalization, library
from app.domain.extraction_runs import utc_now_iso
from app.domain.review import effective_description, effective_name
from app.normalization.canonical_settings import normalize_alias
from app.operations.backup import inspect_backup
from tools.audit_public_bios_knowledge import build_report as build_public_audit

ROOT = Path(__file__).resolve().parents[1]
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.7-pre-wave2.zip"
BACKUP_SHA256 = "443c2f93ad7a3d753b45cba522243491b61a698fd7560907fb9af10d3841a758"
REPORT_JSON = ROOT / "data" / "reports" / "v1.7-cross-vendor-wave2.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.7-cross-vendor-wave2.md"
ACTOR = "v1.7-cross-vendor-official-evidence-reviewer"
CONFIDENCE_BASIS_POINTS = 9990

TCG_TPM_URL = "https://trustedcomputinggroup.org/resource/tpm-library-specification/"
PCI_SIG_ARI_URL = "https://pcisig.com/PCIExpress/ECN/Base/AlternativeRouting-IDInterpretation"
PCI_SIG_SRIOV_URL = (
    "https://pcisig.com/PCIExpress/Specs/IOV/SingleRootIOVirtualizationandSharing_1.0"
)
PCI_SIG_AER_URL = "https://pcisig.com/PCIExpress/ECN/Base/InternalErrorReporting"
UEFI_BOOT_URL = "https://uefi.org/specs/UEFI/2.11/03_Boot_Manager.html"


@dataclass(frozen=True)
class ManualPin:
    manual_id: int
    vendor_id: str
    sha256: str
    source_url: str
    page_count: int


@dataclass(frozen=True)
class SourcePin:
    setting_id: int
    manual_id: int
    page_number: int


@dataclass(frozen=True)
class IdentitySeed:
    canonical_id: str
    canonical_name: str
    description: str
    category_id: str
    risk_level: str
    risk_rationale: str
    sources: tuple[SourcePin, ...]
    rationale: str
    uncertainty: str
    supporting_urls: tuple[str, ...] = ()


def _sources(*values: tuple[int, int, int]) -> tuple[SourcePin, ...]:
    return tuple(SourcePin(*value) for value in values)


MANUALS = (
    ManualPin(
        1,
        "gigabyte",
        "389d5b7ab9330ef95523bccad85afae578fbf91bd97572eff9b438511d14e154",
        "https://www.gigabyte.com/Motherboard/B650M-AORUS-ELITE-AX-rev-14/support#support-manual",
        25,
    ),
    ManualPin(
        2,
        "asrock",
        "ec2ca097b878859f5de664449f5ca8d0d51eb41509290588da0fc2dacf711315",
        "https://www.asrock.com/mb/AMD/X870E%20Taichi%20White/index.asp#Manual",
        85,
    ),
    ManualPin(
        3,
        "asus",
        "586c9e7fc4a78e22afc5234b3d5f13511b9e923131a76f4af963bd4a50293054",
        "https://www.asus.com/us/supportonly/prime%20b860m-ks/helpdesk_manual/",
        92,
    ),
    ManualPin(
        5,
        "msi",
        "c59d17516e594f187bcb8d1a60b9e746f0a1849435f76ae93e726040f731af8e",
        "https://www.msi.com/support/download-manual/MPG-Z790-EDGE-TI-MAX-WIFI",
        71,
    ),
    ManualPin(
        6,
        "msi",
        "5ad148e9ec850442778a3cc090df281660712919299a1c4127d012f780ae622c",
        "https://www.msi.com/Motherboard/MPG-X870E-CARBON-WIFI/support",
        49,
    ),
    ManualPin(
        7,
        "gigabyte",
        "b46c52231334bfe914491cc3d6aa9265631914ba9c1de6bb6aa11841b46a2fe5",
        "https://www.gigabyte.com/Motherboard/Z890-AORUS-ELITE-WIFI7/support#support-manual",
        30,
    ),
    ManualPin(
        8,
        "asus",
        "2e7a4b2d504e9f9286b584fcfbe9cba85e0ccd4a061ad545025160408b308213",
        "https://www.asus.com/us/supportonly/tuf%20gaming%20x870-plus%20wifi/helpdesk_manual/",
        116,
    ),
    ManualPin(
        9,
        "gigabyte",
        "14814b731f11f56522bea71ea506b4182de44e3ad729169b81a075fc84f8bb90",
        "https://www.gigabyte.com/Motherboard/B550-AORUS-ELITE-AX-V2-rev-10/support#support-manual",
        19,
    ),
    ManualPin(
        10,
        "gigabyte",
        "17b5d03f17f9280f024b9ac491c9043b1ed3faa0987ea25a3cbacbf8c631b907",
        "https://www.gigabyte.com/Motherboard/Z690-AORUS-ELITE-AX-rev-14/support#support-manual",
        28,
    ),
    ManualPin(
        11,
        "asus",
        "4e3f39d81ae255a6dc8d48a52a31d0be2e496d457e3c6a17f7c689eb54cb57cc",
        "https://www.asus.com/us/motherboards-components/motherboards/prime/prime-b660-plus-d4/helpdesk_manual?model2Name=PRIME-B660-PLUS-D4",
        88,
    ),
    ManualPin(
        12,
        "asus",
        "49d55cb253e29b523cb69a78934d1df0f8160dd7feb9c68a76ceecb42927da37",
        "https://www.asus.com/motherboards-components/motherboards/tuf-gaming/tuf-gaming-b760m-plus/helpdesk_manual?model2Name=TUF-GAMING-B760M-PLUS",
        94,
    ),
)


NEW_IDENTITIES = (
    IdentitySeed(
        "address-hash-bank",
        "Address Hash Bank",
        "Controls whether DRAM bank address hashing is used.",
        "memory",
        "high",
        "Changing memory address hashing can affect memory behavior, performance, and stability.",
        _sources((427, 2, 60), (5406, 6, 37), (6313, 8, 69)),
        "ASRock, MSI, and ASUS use the exact label and all describe the same bank-address hashing toggle.",
        "Hash implementation details remain memory-controller and firmware specific.",
    ),
    IdentitySeed(
        "address-hash-cs",
        "Address Hash CS",
        "Controls whether DRAM chip-select address hashing is used.",
        "memory",
        "high",
        "Changing chip-select address hashing can affect memory behavior, performance, and stability.",
        _sources((428, 2, 60), (5407, 6, 37), (6314, 8, 69)),
        "ASRock, MSI, and ASUS use the exact label and all describe the same chip-select address hashing toggle.",
        "Hash implementation details remain memory-controller and firmware specific.",
    ),
    IdentitySeed(
        "address-hash-subchannel",
        "Address Hash Subchannel",
        "Controls whether DDR subchannel address hashing is used.",
        "memory",
        "high",
        "Changing subchannel address hashing can affect memory behavior, performance, and stability.",
        _sources((429, 2, 60), (6315, 8, 69)),
        "ASRock and ASUS use the exact label and describe the same subchannel address hashing toggle in equivalent AMD CBS hierarchy.",
        "Availability depends on the memory controller and firmware.",
    ),
    IdentitySeed(
        "dfe-read-training",
        "DFE Read Training",
        "Controls two-dimensional DRAM read training with decision-feedback equalization enabled.",
        "memory",
        "high",
        "Changing DRAM training behavior can cause memory instability or failed training.",
        _sources((249, 2, 35), (432, 2, 60), (5409, 6, 37), (6318, 8, 69)),
        "Two ASRock paths plus MSI and ASUS use the exact label and describe the same 2D read training with DFE.",
        "Training implementation and applicability remain firmware and memory-controller specific.",
    ),
    IdentitySeed(
        "sha256-pcr-bank",
        "SHA256 PCR Bank",
        "Controls availability of the TPM Platform Configuration Register bank that uses SHA-256.",
        "security",
        "high",
        "Disabling a TPM PCR bank can change measured-boot and attestation capabilities.",
        _sources((334, 2, 51), (1114, 3, 62), (4637, 5, 29), (6100, 8, 48), (8220, 12, 64)),
        "ASRock, ASUS, and MSI use the exact label under Trusted Computing and describe the same SHA-256 PCR-bank toggle.",
        "Operating-system and TPM support determine whether the bank is used.",
        (TCG_TPM_URL,),
    ),
    IdentitySeed(
        "sha384-pcr-bank",
        "SHA384 PCR Bank",
        "Controls availability of the TPM Platform Configuration Register bank that uses SHA-384.",
        "security",
        "high",
        "Disabling a TPM PCR bank can change measured-boot and attestation capabilities.",
        _sources((335, 2, 51), (4638, 5, 29)),
        "ASRock and MSI use the exact label under Trusted Computing and describe the same SHA-384 PCR-bank toggle.",
        "Operating-system and TPM support determine whether the bank is used.",
        (TCG_TPM_URL,),
    ),
    IdentitySeed(
        "sm3-256-pcr-bank",
        "SM3_256 PCR Bank",
        "Controls availability of the TPM Platform Configuration Register bank that uses SM3-256.",
        "security",
        "high",
        "Disabling a TPM PCR bank can change measured-boot and attestation capabilities.",
        _sources((4639, 5, 29), (6102, 8, 49)),
        "MSI and ASUS use the exact label under Trusted Computing and describe the same SM3-256 PCR-bank toggle.",
        "Operating-system and TPM support determine whether the bank is used.",
        (TCG_TPM_URL,),
    ),
    IdentitySeed(
        "endorsement-hierarchy",
        "Endorsement Hierarchy",
        "Controls availability of the TPM endorsement hierarchy.",
        "security",
        "high",
        "Disabling a TPM hierarchy can make endorsement-based keys and services unavailable.",
        _sources(
            (339, 2, 52), (1118, 3, 62), (4643, 5, 29), (5196, 6, 20), (6106, 8, 49), (8224, 12, 65)
        ),
        "ASRock, ASUS, and MSI use the exact label and matching enable/disable behavior under Trusted Computing.",
        "Exact platform provisioning policies remain vendor specific.",
        (TCG_TPM_URL,),
    ),
    IdentitySeed(
        "platform-hierarchy",
        "Platform Hierarchy",
        "Controls availability of the TPM platform hierarchy.",
        "security",
        "high",
        "Disabling the TPM platform hierarchy changes firmware-controlled TPM capabilities and authorization behavior.",
        _sources(
            (337, 2, 52), (1116, 3, 62), (4641, 5, 29), (5194, 6, 20), (6104, 8, 49), (8222, 12, 64)
        ),
        "ASRock, ASUS, and MSI use the exact label and matching enable/disable behavior under Trusted Computing.",
        "Exact platform authorization policies remain vendor specific.",
        (TCG_TPM_URL,),
    ),
    IdentitySeed(
        "storage-hierarchy",
        "Storage Hierarchy",
        "Controls availability of the TPM storage hierarchy.",
        "security",
        "high",
        "Disabling the TPM storage hierarchy can make owner storage keys and protected objects unavailable.",
        _sources(
            (338, 2, 52), (1117, 3, 62), (4642, 5, 29), (5195, 6, 20), (6105, 8, 49), (8223, 12, 64)
        ),
        "ASRock, ASUS, and MSI use the exact label and matching enable/disable behavior under Trusted Computing.",
        "Exact owner provisioning policies remain vendor specific.",
        (TCG_TPM_URL,),
    ),
    IdentitySeed(
        "pending-operation",
        "Pending Operation",
        "Schedules the next firmware-mediated TPM operation, such as clearing the TPM.",
        "security",
        "high",
        "A pending TPM clear can remove TPM-protected data and affect encrypted-storage recovery.",
        _sources(
            (336, 2, 51), (1115, 3, 62), (4640, 5, 29), (5193, 6, 20), (6103, 8, 49), (8221, 12, 64)
        ),
        "ASRock, ASUS, and MSI use the exact label, the same None/TPM Clear choices, and the same reboot behavior.",
        "Additional operations may appear on other firmware versions.",
        (TCG_TPM_URL,),
    ),
    IdentitySeed(
        "security-device-support",
        "Security Device Support",
        "Controls firmware support for the platform security device or TPM interface.",
        "security",
        "high",
        "Disabling firmware TPM support can make TPM-backed security, measured boot, and protected keys unavailable.",
        _sources(
            (331, 2, 51), (1113, 3, 62), (4635, 5, 29), (5186, 6, 19), (6098, 8, 48), (8219, 12, 64)
        ),
        "ASRock, ASUS, and MSI use the exact label in Trusted Computing; all describe firmware TPM/security-device availability.",
        "MSI emphasizes TPM endorsement-key access while ASUS and ASRock describe protocol visibility; both are consequences of the same control.",
        (TCG_TPM_URL,),
    ),
    IdentitySeed(
        "physical-presence-spec-version",
        "Physical Presence Spec Version",
        "Selects the TPM Physical Presence Interface specification version exposed to the operating system.",
        "security",
        "moderate",
        "Changing the exposed PPI version can affect operating-system compatibility with TPM management actions.",
        _sources((340, 2, 52), (1119, 3, 63), (4644, 5, 29), (6107, 8, 49), (8225, 12, 65)),
        "ASRock, ASUS, and MSI use the same label under Trusted Computing and identify the same PPI specification selection; four sources explicitly list versions 1.2 and 1.3.",
        "MSI does not enumerate choices in the extracted description, but its label and hierarchy agree.",
        (TCG_TPM_URL,),
    ),
    IdentitySeed(
        "sr-iov-support",
        "SR-IOV Support",
        "Enables or disables firmware support for PCI Express Single Root I/O Virtualization on capable devices.",
        "virtualization",
        "moderate",
        "Changing SR-IOV support can affect virtual-function enumeration, device assignment, and isolation behavior.",
        _sources(
            (84, 1, 15), (311, 2, 44), (1122, 3, 63), (6116, 8, 51), (7572, 11, 60), (8229, 12, 66)
        ),
        "GIGABYTE, ASRock, and ASUS use the exact label and explicitly describe Single Root I/O Virtualization for capable PCIe devices.",
        "Devices, operating systems, and hypervisors must also support SR-IOV.",
        (PCI_SIG_SRIOV_URL,),
    ),
    IdentitySeed(
        "usb-mass-storage-driver-support",
        "USB Mass Storage Driver Support",
        "Enables or disables firmware driver support for USB mass-storage devices.",
        "peripheral-io",
        "moderate",
        "Disabling firmware USB storage support can prevent USB storage detection or boot before the operating system loads.",
        _sources(
            (91, 1, 15), (330, 2, 50), (5635, 7, 21), (6119, 8, 51), (6810, 9, 11), (7000, 10, 20)
        ),
        "GIGABYTE, ASRock, and ASUS use the exact label and all describe firmware support for USB storage devices.",
        "Legacy USB policy and operating-system drivers are separate controls.",
    ),
    IdentitySeed(
        "pcie-ari-support",
        "PCIe ARI Support",
        "Controls support for PCI Express Alternative Routing-ID Interpretation.",
        "pcie-resource",
        "moderate",
        "Changing ARI support can affect PCIe function enumeration and virtualization-capable devices.",
        _sources((468, 2, 63), (5287, 6, 29), (6355, 8, 72)),
        "ASRock, MSI, and ASUS use the exact label and identify the same Alternative Routing-ID Interpretation support control.",
        "Effective behavior depends on downstream-device and operating-system support.",
        (PCI_SIG_ARI_URL,),
    ),
    IdentitySeed(
        "pcie-ari-enumeration",
        "PCIe ARI Enumeration",
        "Controls ARI forwarding or enumeration for PCI Express downstream ports.",
        "pcie-resource",
        "moderate",
        "Changing ARI enumeration can affect how PCIe functions behind downstream ports are discovered.",
        _sources((471, 2, 64), (5288, 6, 29), (6358, 8, 72)),
        "ASRock, MSI, and ASUS use the exact label; ASRock and ASUS explicitly identify downstream-port ARI forwarding while MSI names ARI enumeration.",
        "Port-specific implementation remains firmware and topology dependent.",
        (PCI_SIG_ARI_URL,),
    ),
    IdentitySeed(
        "advanced-error-reporting-aer",
        "Advanced Error Reporting (AER)",
        "Controls firmware support for PCI Express Advanced Error Reporting.",
        "pcie-resource",
        "moderate",
        "Disabling AER support can reduce PCIe error reporting and diagnostic visibility.",
        _sources((470, 2, 63), (6357, 8, 72)),
        "ASRock and ASUS use the exact label, equivalent choices, and the same AMD CBS PCIe hierarchy.",
        "Operating-system ownership of AER can affect runtime behavior.",
        (PCI_SIG_AER_URL,),
    ),
    IdentitySeed(
        "pcie-all-port-ecrc",
        "PCIe All Port ECRC",
        "Controls end-to-end CRC support across PCI Express ports where supported.",
        "pcie-resource",
        "moderate",
        "Changing ECRC support can affect PCIe data-integrity checking and compatibility.",
        _sources((469, 2, 63), (6356, 8, 72)),
        "ASRock and ASUS use the exact label, equivalent choices, and the same AMD CBS PCIe hierarchy.",
        "Device and port support determine whether ECRC is effective.",
    ),
    IdentitySeed(
        "system-date",
        "System Date",
        "Sets the date maintained by the firmware real-time clock.",
        "uncategorized",
        "low",
        "An incorrect firmware date can affect logs, certificates, and operating-system time initialization.",
        _sources(
            (131, 1, 21),
            (678, 3, 16),
            (4496, 5, 11),
            (5107, 6, 11),
            (5682, 7, 27),
            (5771, 8, 15),
            (6855, 9, 15),
            (7039, 10, 24),
        ),
        "GIGABYTE, ASUS, and MSI use the exact label and consistently describe setting the firmware date.",
        "Display format and editing keys vary by vendor.",
    ),
    IdentitySeed(
        "system-time",
        "System Time",
        "Sets the time maintained by the firmware real-time clock.",
        "uncategorized",
        "low",
        "An incorrect firmware time can affect logs, certificates, and operating-system time initialization.",
        _sources(
            (132, 1, 21),
            (679, 3, 16),
            (4497, 5, 11),
            (5108, 6, 11),
            (5495, 7, 4),
            (5683, 7, 27),
            (5772, 8, 15),
            (6856, 9, 15),
            (7040, 10, 24),
        ),
        "GIGABYTE, ASUS, and MSI use the exact label and consistently describe setting the firmware time; both GIGABYTE main-menu observations are retained.",
        "Display format and editing keys vary by vendor.",
    ),
    IdentitySeed(
        "boot-override",
        "Boot Override",
        "Selects an available boot device for an immediate one-time boot attempt.",
        "boot",
        "moderate",
        "Selecting an unintended device can boot different software or installation media for the current attempt.",
        _sources(
            (160, 1, 25),
            (1276, 3, 84),
            (4683, 5, 32),
            (5237, 6, 24),
            (5709, 7, 30),
            (6724, 8, 108),
            (6883, 9, 19),
            (7069, 10, 28),
        ),
        "GIGABYTE, ASUS, and MSI use the exact label and all describe immediately booting from a selected available device.",
        "Confirmation and restart behavior vary by vendor.",
        (UEFI_BOOT_URL,),
    ),
    IdentitySeed(
        "boot-option-priorities",
        "Boot Option Priorities",
        "Sets the ordered priority of available firmware boot options.",
        "boot",
        "moderate",
        "Changing boot priority can alter which operating system, device, or recovery environment starts.",
        _sources(
            (138, 1, 22),
            (1275, 3, 84),
            (5176, 6, 18),
            (5691, 7, 28),
            (6723, 8, 108),
            (6861, 9, 16),
            (7048, 10, 25),
            (7692, 11, 80),
            (8355, 12, 85),
        ),
        "GIGABYTE, ASUS, and MSI use the exact label and consistently describe ordering installed or available boot devices.",
        "Displayed entries depend on installed devices and firmware policy.",
        (UEFI_BOOT_URL,),
    ),
)


EXISTING_IDENTITIES = (
    IdentitySeed(
        "administrator-password",
        "Administrator Password",
        "",
        "",
        "",
        "",
        _sources(
            (681, 3, 17),
            (4627, 5, 28),
            (5701, 7, 29),
            (6875, 9, 17),
            (7061, 10, 27),
            (7134, 11, 16),
            (7774, 12, 18),
        ),
        "The exact label, full firmware-setup privilege role, and Security/Main/Boot context agree with the existing identity.",
        "Password prompts and recovery procedures vary by vendor.",
    ),
    IdentitySeed(
        "user-password",
        "User Password",
        "",
        "",
        "",
        "",
        _sources(
            (682, 3, 17),
            (4628, 5, 28),
            (5702, 7, 29),
            (6876, 9, 18),
            (7062, 10, 27),
            (7135, 11, 16),
            (7775, 12, 18),
        ),
        "The exact label, limited firmware-setup privilege role, and Security/Main/Boot context agree with the existing identity.",
        "Password prompts and recovery procedures vary by vendor.",
    ),
    IdentitySeed(
        "hd-audio-controller",
        "HD Audio Controller",
        "",
        "",
        "",
        "",
        _sources((4558, 5, 19), (6799, 9, 10)),
        "The exact label and onboard high-definition audio controller toggle agree with the existing ASUS, MSI, and GIGABYTE identity.",
        "Codec implementation remains board specific.",
    ),
    IdentitySeed(
        "nvme-raid-mode",
        "NVMe RAID Mode",
        "",
        "",
        "",
        "",
        _sources((6816, 9, 11)),
        "The exact normalized label and use of NVMe PCIe SSDs for RAID agree with the existing GIGABYTE, ASRock, and ASUS identity.",
        "Driver, lane, and boot requirements remain platform specific.",
    ),
    IdentitySeed(
        "system-language",
        "System Language",
        "",
        "",
        "",
        "",
        _sources((677, 3, 16), (5681, 7, 27), (6854, 9, 15), (7038, 10, 24)),
        "The exact label and firmware interface-language behavior agree with the existing identity.",
        "Available languages vary by firmware.",
    ),
    IdentitySeed(
        "usb-power-delivery-in-soft-off-state-s5",
        "USB Power Delivery in Soft Off State (S5)",
        "",
        "",
        "",
        "",
        _sources((1164, 3, 68), (7586, 11, 65), (8243, 12, 70)),
        "The exact normalized label, S5 USB-power behavior, and onboard-device context agree with the existing ASRock and ASUS identity.",
        "Powered ports remain board specific.",
    ),
)

ALL_DECISIONS = (*NEW_IDENTITIES, *EXISTING_IDENTITIES)

TPM_GROUPS = {
    "sha256-pcr-bank",
    "sha384-pcr-bank",
    "sm3-256-pcr-bank",
    "endorsement-hierarchy",
    "platform-hierarchy",
    "storage-hierarchy",
    "pending-operation",
    "security-device-support",
    "physical-presence-spec-version",
}
MEMORY_GROUPS = {
    "address-hash-bank",
    "address-hash-cs",
    "address-hash-subchannel",
    "dfe-read-training",
}
PCIE_GROUPS = {
    "pcie-ari-support",
    "pcie-ari-enumeration",
    "advanced-error-reporting-aer",
    "pcie-all-port-ecrc",
}

TPM_PARENTS = {
    2: "Advanced > Trusted Computing",
    3: "Advanced > Trusted Computing",
    5: "Settings > Security > Trusted Computing",
    6: "Settings > Security > Trusted Computing",
    8: "Advanced > Trusted Computing",
    12: "Advanced > Trusted Computing",
}
SRIOV_PARENTS = {
    1: "Settings > IO Ports",
    2: "Advanced > PCI Configuration",
    3: "Advanced > PCI Subsystem Settings",
    8: "Advanced > PCI Subsystem Settings",
    11: "Advanced > PCI Subsystem Settings",
    12: "Advanced > PCI Subsystem Settings",
}
USB_STORAGE_PARENTS = {
    1: "Settings > IO Ports > USB Configuration",
    2: "Advanced > USB Configuration",
    7: "Settings > IO Ports > USB Configuration",
    8: "Advanced > USB Configuration",
    9: "Settings > IO Ports > USB Configuration",
    10: "Settings > IO Ports > USB Configuration",
}
PCI_PARENTS = {
    2: "Advanced > AMD CBS > NBIO Common Options",
    6: "OC > Advanced CPU Configuration > AMD CBS > NBIO Common Options",
    8: "Advanced > AMD CBS > NBIO Common Options",
}
DATE_TIME_PARENTS = {
    1: "System Info",
    3: "Main",
    5: "Settings > System Status",
    6: "Settings > System Status",
    7: "System Info",
    8: "Main",
    9: "System Info",
    10: "System Information",
}
BOOT_PRIORITY_PARENTS = {
    1: "Boot",
    3: "Boot",
    6: "Settings > Boot",
    7: "Boot",
    8: "Boot",
    9: "Boot",
    10: "Boot",
    11: "Boot",
    12: "Boot",
}
BOOT_OVERRIDE_PARENTS = {
    1: "Save & Exit",
    3: "Boot",
    5: "Settings > Save & Exit",
    6: "Settings > Save & Exit",
    7: "Save & Exit",
    8: "Boot",
    9: "Save & Exit",
    10: "Save & Exit",
}
PASSWORD_PARENTS = {
    3: "Main",
    5: "Settings > Security",
    7: "Boot",
    9: "Boot",
    10: "Boot",
    11: "Main",
    12: "Main",
}
SYSTEM_LANGUAGE_PARENTS = {3: "Main", 7: "System Info", 9: "System Info", 10: "System Information"}
USB_S5_PARENTS = {
    3: "Advanced > Onboard Devices Configuration",
    11: "Advanced > Onboard Devices Configuration",
    12: "Advanced > Onboard Devices Configuration",
}

PATH_OVERRIDES = {
    249: "OC Tweaker > DRAM Profile Configuration > DRAM Timing Configuration > DDR Training Options > DFE Read Training",
    432: "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Training Options > DFE Read Training",
    5406: "OC > Advanced DRAM Configuration > Address Hash Bank",
    5407: "OC > Advanced DRAM Configuration > Address Hash CS",
    5409: "OC > Advanced DRAM Configuration > DFE Read Training",
    6313: "Advanced > AMD CBS > UMC Common Options > DDR Controller Configuration > DDR Addressing Options > Address Hash Bank",
    6314: "Advanced > AMD CBS > UMC Common Options > DDR Controller Configuration > DDR Addressing Options > Address Hash CS",
    6315: "Advanced > AMD CBS > UMC Common Options > DDR Controller Configuration > DDR Addressing Options > Address Hash Subchannel",
    6318: "Advanced > AMD CBS > UMC Common Options > DDR Controller Configuration > DDR Training Options > DFE Read Training",
    427: "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Addressing Options > Address Hash Bank",
    428: "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Addressing Options > Address Hash CS",
    429: "Advanced > AMD CBS > UMC Common Options > DDR Options > DDR Addressing Options > Address Hash Subchannel",
    5495: "Main Menu > System Time",
    4558: "Settings > Advanced > Integrated Peripherals > HD Audio Controller",
    6799: "Settings > IO Ports > HD Audio Controller",
    6816: "Settings > IO Ports > NVMe RAID mode",
}

BASELINE_COUNTS = {
    CanonicalSetting: 68,
    CanonicalSettingAlias: 104,
    SettingCanonicalMapping: 285,
    CanonicalMappingEvent: 285,
    CanonicalIdentityEvent: 83,
    CanonicalSettingClassification: 68,
    CanonicalClassificationEvent: 97,
    SettingMenuContext: 82,
    SettingMenuContextEvent: 82,
    SettingMenuContextCitation: 156,
}

NOTABLE_EXCLUSIONS = (
    {
        "label": "PSS Support",
        "reason": "ASUS record 6111 omits the _PSS ACPI object documented by the existing identity; it remains unmapped.",
    },
    {
        "label": "PPT Control",
        "reason": "The same label is used both for Package Power Tracking limits and for a DDR training-pattern control; exact naming is unsafe.",
    },
    {
        "label": "Integrated Graphics",
        "reason": "Official descriptions disagree between controller enablement and UMA memory-allocation behavior.",
    },
    {
        "label": "Global C-state Control",
        "reason": "One official source describes general CPU C-states while others specifically describe I/O-generated and Data Fabric C-states.",
    },
    {
        "label": "Clear Secure Boot Keys",
        "reason": "The previously reviewed ASRock hierarchy remains ambiguous; the proposal stays rejected.",
    },
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _count(session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _unmapped_count(session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Setting)
            .outerjoin(SettingCanonicalMapping, SettingCanonicalMapping.setting_id == Setting.id)
            .where(Setting.status == "approved", SettingCanonicalMapping.setting_id.is_(None))
        )
        or 0
    )


def _source_ids() -> tuple[int, ...]:
    return tuple(source.setting_id for decision in ALL_DECISIONS for source in decision.sources)


def _menu_path(decision: IdentitySeed, source: SourcePin, setting_name: str) -> str:
    if source.setting_id in PATH_OVERRIDES:
        return PATH_OVERRIDES[source.setting_id]
    if decision.canonical_id in TPM_GROUPS:
        parent = TPM_PARENTS[source.manual_id]
    elif decision.canonical_id in MEMORY_GROUPS:
        raise RuntimeError(f"Missing explicit memory menu path for setting {source.setting_id}.")
    elif decision.canonical_id == "sr-iov-support":
        parent = SRIOV_PARENTS[source.manual_id]
    elif decision.canonical_id == "usb-mass-storage-driver-support":
        parent = USB_STORAGE_PARENTS[source.manual_id]
    elif decision.canonical_id in PCIE_GROUPS:
        parent = PCI_PARENTS[source.manual_id]
    elif decision.canonical_id in {"system-date", "system-time"}:
        parent = DATE_TIME_PARENTS[source.manual_id]
    elif decision.canonical_id == "boot-option-priorities":
        parent = BOOT_PRIORITY_PARENTS[source.manual_id]
    elif decision.canonical_id == "boot-override":
        parent = BOOT_OVERRIDE_PARENTS[source.manual_id]
    elif decision.canonical_id in {"administrator-password", "user-password"}:
        parent = PASSWORD_PARENTS[source.manual_id]
    elif decision.canonical_id == "system-language":
        parent = SYSTEM_LANGUAGE_PARENTS[source.manual_id]
    elif decision.canonical_id == "usb-power-delivery-in-soft-off-state-s5":
        parent = USB_S5_PARENTS[source.manual_id]
    else:
        raise RuntimeError(f"No reviewed path rule for {decision.canonical_id}.")
    return f"{parent} > {setting_name}"


def _verify_backup() -> None:
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.7 backup is missing or its checksum changed.")
    if inspect_backup(BACKUP_PATH).database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The pre-v1.7 backup has an unexpected database revision.")


def _verify_manifest() -> None:
    ids = _source_ids()
    if len(NEW_IDENTITIES) != 23 or len(EXISTING_IDENTITIES) != 6:
        raise RuntimeError("Wave 2 must contain 23 new and six existing identity decisions.")
    if len(ids) != 136 or len(set(ids)) != 136:
        raise RuntimeError("Wave 2 must contain 136 unique source settings.")
    if len({seed.canonical_id for seed in ALL_DECISIONS}) != len(ALL_DECISIONS):
        raise RuntimeError("Wave 2 identity IDs are not unique.")


def _verify_baseline(session) -> None:
    revision = session.scalar(text("SELECT version_num FROM alembic_version"))
    if revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError(
            f"Expected revision {config.EXPECTED_DATABASE_REVISION}, found {revision}."
        )
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity check failed before Wave 2.")
    if _unmapped_count(session) != 3733:
        raise RuntimeError(
            f"Expected 3,733 approved unmapped settings, found {_unmapped_count(session)}."
        )
    for model, expected in BASELINE_COUNTS.items():
        found = _count(session, model)
        if found != expected:
            raise RuntimeError(f"{model.__tablename__}: expected {expected}, found {found}.")
    for pin in MANUALS:
        manual = session.get(Manual, pin.manual_id)
        actual = (
            manual.vendor_id if manual else None,
            manual.sha256 if manual else None,
            manual.source_url if manual else None,
            manual.page_count if manual else None,
        )
        expected = (pin.vendor_id, pin.sha256, pin.source_url, pin.page_count)
        if actual != expected:
            raise RuntimeError(f"Pinned manual {pin.manual_id} changed: {actual!r}.")
    rows = {
        setting.id: (setting, manual)
        for setting, manual in session.execute(
            select(Setting, Manual)
            .join(Manual, Manual.id == Setting.manual_id)
            .where(Setting.id.in_(_source_ids()))
        )
    }
    if len(rows) != 136:
        raise RuntimeError("One or more Wave 2 source settings are missing.")
    for decision in ALL_DECISIONS:
        for source in decision.sources:
            setting, manual = rows[source.setting_id]
            actual = (
                setting.manual_id,
                setting.edited_page_number or setting.page_number,
                normalize_alias(effective_name(setting)),
            )
            expected = (
                source.manual_id,
                source.page_number,
                normalize_alias(decision.canonical_name),
            )
            if actual != expected:
                raise RuntimeError(
                    f"Pinned source {setting.id} changed: expected {expected}, found {actual}."
                )
            reasons = canonicalization.source_trust_reasons(setting, manual)
            if reasons:
                raise RuntimeError(
                    f"Pinned source {setting.id} failed trust gates: {' '.join(reasons)}"
                )
            if session.get(SettingCanonicalMapping, setting.id) is not None:
                raise RuntimeError(f"Pinned source {setting.id} is already mapped.")
            if session.get(SettingMenuContext, setting.id) is not None:
                raise RuntimeError(f"Pinned source {setting.id} already has a menu context.")
            _menu_path(decision, source, effective_name(setting))
    for seed in NEW_IDENTITIES:
        if session.get(CanonicalSetting, seed.canonical_id) is not None:
            raise RuntimeError(f"New identity already exists: {seed.canonical_id}.")
    for seed in EXISTING_IDENTITIES:
        identity = session.get(CanonicalSetting, seed.canonical_id)
        if identity is None or identity.status != canonical_settings.IDENTITY_ACTIVE:
            raise RuntimeError(f"Existing identity is unavailable: {seed.canonical_id}.")
    evaluation = canonicalization.evaluate_backlog(session)
    if any(entry.automatic_eligible for entry in evaluation.entries):
        raise RuntimeError("An unexpected automatic mapping is eligible before Wave 2.")


def _record_contexts(session) -> None:
    for decision in ALL_DECISIONS:
        for source in decision.sources:
            setting = session.get(Setting, source.setting_id)
            path = _menu_path(decision, source, effective_name(setting))
            canonical_identity_review.review_menu_context(
                session,
                setting.id,
                menu_path=path,
                source_page_number=source.page_number,
                evidence_excerpt=f"The official manual page lists {effective_name(setting)} in the visually reviewed hierarchy represented by this path.",
                note="v1.7 Wave 2 official-manual page and ordered menu hierarchy visually verified.",
                actor=ACTOR,
                commit=False,
            )


def _mapping_note(session, decision: IdentitySeed, source: SourcePin) -> str:
    manual = session.get(Manual, source.manual_id)
    context = session.get(SettingMenuContext, source.setting_id)
    support = (
        "; supporting primary specification(s): " + ", ".join(decision.supporting_urls)
        if decision.supporting_urls
        else ""
    )
    return (
        f"v1.7 Wave 2 reviewer decision; confidence={CONFIDENCE_BASIS_POINTS / 100:.2f}%. "
        f"{decision.rationale} Official source: {manual.vendor} manual {manual.id}, page {source.page_number}, {manual.source_url}; "
        f"reviewed menu path: {context.menu_path}{support}. Remaining uncertainty: {decision.uncertainty}"
    )


def _apply_decisions(session) -> None:
    for seed in NEW_IDENTITIES:
        identity = canonical_settings.create_identity(
            session,
            name=seed.canonical_name,
            canonical_id=seed.canonical_id,
            description=seed.description,
            actor=ACTOR,
            commit=False,
        )
        canonical_settings.classify_identity(
            session,
            identity,
            category_id=seed.category_id,
            risk_level=seed.risk_level,
            risk_rationale=seed.risk_rationale,
            actor=ACTOR,
            commit=False,
        )
        for source in seed.sources:
            canonical_settings.map_setting(
                session,
                session.get(Setting, source.setting_id),
                canonical_id=seed.canonical_id,
                note=_mapping_note(session, seed, source),
                actor=ACTOR,
                mapping_method=canonical_settings.MAPPING_REVIEWER,
                commit=False,
            )
    for seed in EXISTING_IDENTITIES:
        for source in seed.sources:
            canonical_settings.map_setting(
                session,
                session.get(Setting, source.setting_id),
                canonical_id=seed.canonical_id,
                note=_mapping_note(session, seed, source),
                actor=ACTOR,
                mapping_method=canonical_settings.MAPPING_REVIEWER,
                commit=False,
            )


def _verify_final(session, baseline: dict[type, int]) -> None:
    deltas = {
        CanonicalSetting: 23,
        CanonicalSettingAlias: 23,
        SettingCanonicalMapping: 136,
        CanonicalMappingEvent: 136,
        CanonicalIdentityEvent: 23,
        CanonicalSettingClassification: 23,
        CanonicalClassificationEvent: 46,
        SettingMenuContext: 136,
        SettingMenuContextEvent: 136,
        SettingMenuContextCitation: 136,
    }
    failures: list[str] = []
    for model, delta in deltas.items():
        found = _count(session, model)
        expected = baseline[model] + delta
        if found != expected:
            failures.append(f"{model.__tablename__}: expected {expected}, found {found}")
    if _unmapped_count(session) != 3597:
        failures.append(f"approved unmapped: expected 3597, found {_unmapped_count(session)}")
    for seed in ALL_DECISIONS:
        expected_ids = {source.setting_id for source in seed.sources}
        found_ids = set(
            session.scalars(
                select(SettingCanonicalMapping.setting_id).where(
                    SettingCanonicalMapping.canonical_id == seed.canonical_id,
                    SettingCanonicalMapping.setting_id.in_(expected_ids),
                )
            )
        )
        if found_ids != expected_ids:
            failures.append(f"{seed.canonical_id}: Wave 2 mappings differ from the manifest")
        detail = library.canonical_detail(session, seed.canonical_id)
        if detail is None or not expected_ids.issubset(
            {item.setting_id for item in detail.source_settings}
        ):
            failures.append(f"{seed.canonical_id}: public source projection mismatch")
        elif any(
            not item.menu_path for item in detail.source_settings if item.setting_id in expected_ids
        ):
            failures.append(f"{seed.canonical_id}: reviewed public menu path missing")
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        failures.append("SQLite integrity_check failed")
    foreign_keys = list(session.execute(text("PRAGMA foreign_key_check")))
    if foreign_keys:
        failures.append(f"foreign-key violations: {foreign_keys!r}")
    if failures:
        raise RuntimeError("v1.7 Wave 2 verification failed:\n- " + "\n- ".join(failures))


def _source_payload(session, decision: IdentitySeed, source: SourcePin) -> dict[str, object]:
    setting = session.get(Setting, source.setting_id)
    manual = session.get(Manual, source.manual_id)
    context = session.get(SettingMenuContext, source.setting_id)
    return {
        "setting_id": setting.id,
        "canonical_id": decision.canonical_id,
        "source_name": effective_name(setting),
        "source_description": effective_description(setting),
        "vendor_id": manual.vendor_id,
        "manual_id": manual.id,
        "manual_title": manual.document_title,
        "official_url": manual.source_url,
        "page_number": source.page_number,
        "menu_path": context.menu_path,
        "confidence_percent": CONFIDENCE_BASIS_POINTS / 100,
    }


def _remaining_payload(session) -> list[dict[str, object]]:
    rows = session.execute(
        select(Setting, Manual)
        .join(Manual, Manual.id == Setting.manual_id)
        .outerjoin(SettingCanonicalMapping, SettingCanonicalMapping.setting_id == Setting.id)
        .where(Setting.status == "approved", SettingCanonicalMapping.setting_id.is_(None))
        .order_by(Setting.id)
    )
    return [
        {
            "setting_id": setting.id,
            "vendor_id": manual.vendor_id,
            "manual_id": manual.id,
            "source_name": effective_name(setting),
            "page_number": setting.edited_page_number or setting.page_number,
            "review_status": "pending_evidence_review",
            "reason": "Not approved in Wave 2; requires a later one-by-one official-evidence disposition.",
        }
        for setting, manual in rows
    ]


def _build_payload(session) -> dict[str, object]:
    decisions = []
    for seed in ALL_DECISIONS:
        decisions.append(
            {
                "canonical_id": seed.canonical_id,
                "canonical_name": seed.canonical_name,
                "new_identity": seed in NEW_IDENTITIES,
                "rationale": seed.rationale,
                "uncertainty": seed.uncertainty,
                "supporting_urls": list(seed.supporting_urls),
                "confidence_percent": CONFIDENCE_BASIS_POINTS / 100,
                "sources": [_source_payload(session, seed, source) for source in seed.sources],
            }
        )
    remaining = _remaining_payload(session)
    audit = build_public_audit(session)
    if audit["status"] != "pass":
        raise RuntimeError("The public BIOS knowledge audit failed after Wave 2.")
    return {
        "milestone": "v1.7 cross-vendor canonicalization Wave 2",
        "generated_at": utc_now_iso(),
        "actor": ACTOR,
        "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "summary": {
            "new_canonical_settings": 23,
            "existing_identities_expanded": 6,
            "total_mappings_created": 136,
            "reviewed_menu_contexts": 136,
            "remaining_manual_review": len(remaining),
        },
        "official_manuals": [asdict(pin) for pin in MANUALS],
        "supporting_primary_specifications": [
            TCG_TPM_URL,
            PCI_SIG_ARI_URL,
            PCI_SIG_SRIOV_URL,
            PCI_SIG_AER_URL,
            UEFI_BOOT_URL,
        ],
        "approved_decisions": decisions,
        "notable_exclusions": list(NOTABLE_EXCLUSIONS),
        "remaining_manual_review": remaining,
        "public_knowledge_audit": audit,
    }


def _markdown(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.7 Cross-Vendor Canonicalization Wave 2",
        "",
        "## Outcome",
        "",
        f"- New canonical settings: {summary['new_canonical_settings']}",
        f"- Existing identities expanded: {summary['existing_identities_expanded']}",
        f"- New verified mappings: {summary['total_mappings_created']}",
        f"- New reviewed menu contexts: {summary['reviewed_menu_contexts']}",
        f"- Remaining approved unmapped settings: {summary['remaining_manual_review']}",
        "",
        "All mapping decisions use visually reviewed official manual pages as primary evidence. TCG, PCI-SIG, and UEFI specifications are supporting primary evidence only; no community source is used as the sole basis for approval.",
        "",
        "## Approved identities",
        "",
    ]
    for decision in payload["approved_decisions"]:
        vendors = sorted({source["vendor_id"] for source in decision["sources"]})
        mode = "new" if decision["new_identity"] else "expanded"
        lines.append(
            f"- **{decision['canonical_name']}** ({mode}) - {len(decision['sources'])} mappings across {len(vendors)} vendors: {', '.join(vendors)}. {decision['rationale']}"
        )
    lines.extend(["", "## Notable exclusions", ""])
    for exclusion in payload["notable_exclusions"]:
        lines.append(f"- **{exclusion['label']}** - {exclusion['reason']}")
    lines.extend(
        [
            "",
            "The machine-readable report contains every source decision and the complete pending backlog: `data/reports/v1.7-cross-vendor-wave2.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_temp(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    return temporary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the pinned v1.7 Wave 2 batch")
    args = parser.parse_args()
    _verify_manifest()
    _verify_backup()
    with SessionLocal() as session:
        _verify_baseline(session)
        print(
            "Preflight passed: backup, revision, counts, manuals, 136 source pins, and trust gates match."
        )
        print(
            "Planned outcome: 23 new identities, six expanded identities, 136 mappings and menu contexts."
        )
        if not args.apply:
            print("Dry run only; pass --apply to commit the pinned reviewed batch.")
            return 0
        baseline = {model: _count(session, model) for model in BASELINE_COUNTS}
        json_temp: Path | None = None
        markdown_temp: Path | None = None
        try:
            _record_contexts(session)
            _apply_decisions(session)
            session.flush()
            _verify_final(session, baseline)
            payload = _build_payload(session)
            json_temp = _write_temp(
                REPORT_JSON, json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
            )
            markdown_temp = _write_temp(REPORT_MARKDOWN, _markdown(payload))
            session.commit()
            os.replace(json_temp, REPORT_JSON)
            os.replace(markdown_temp, REPORT_MARKDOWN)
        except Exception:
            session.rollback()
            for temporary in (json_temp, markdown_temp):
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            raise
        print("Applied: new_identities=23, expanded_identities=6, mappings=136, menu_contexts=136.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
