class UploadRejectedError(Exception):
    """Upload rejected by validation; message is safe to show the reviewer."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ManualNotFoundError(Exception):
    """No manual exists with the requested id; message is safe to show."""

    def __init__(self, manual_id: int) -> None:
        super().__init__(f"Manual {manual_id} was not found.")
        self.manual_id = manual_id
        self.message = f"Manual {manual_id} was not found."


class ManualDeletionError(Exception):
    """Manual deletion failed; message is safe to show the reviewer."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ManualProvenanceError(Exception):
    """Manual provenance metadata failed validation; safe to show."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ManualEligibilityError(Exception):
    """Manual eligibility decision failed validation; safe to show."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AcceptanceError(Exception):
    """Acceptance data failed validation; message is safe to show."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ReviewActionError(Exception):
    """Review action rejected by the state machine; message is safe to show."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CanonicalSettingError(Exception):
    """Canonical-setting operation failed validation; safe to show."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class EvidenceSourceError(Exception):
    """Trusted-evidence source intake or review failed validation."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class GuidanceClaimError(Exception):
    """Guidance claim creation or review failed validation."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class GuidanceReadError(Exception):
    """Citation-first guidance lookup could not be resolved safely."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ReferralPointerError(Exception):
    """Official referral registration or retirement failed validation."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class GuidedAnswerError(Exception):
    """A fixed guided question or its reviewed answer contract is invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SystemContextError(Exception):
    """A requested hardware context could not be resolved safely."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DuplicateManualError(UploadRejectedError):
    """Same file content (SHA-256) already exists as a manual (FR-4)."""

    def __init__(self, existing_id: int) -> None:
        super().__init__("This PDF has already been uploaded.")
        self.existing_id = existing_id
