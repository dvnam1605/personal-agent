"""Deterministic Google Contacts (People API) adapter."""

from typing import Any
from urllib.parse import quote

from pydantic import ValidationError as PydanticValidationError

from app.domain.errors import ExternalServiceError
from app.domain.errors import ValidationError as DomainValidationError
from app.domain.models import Contact, ContactEmail, ContactPage, ContactPhone
from app.integrations.google_common import (
    GoogleResourceAdapter,
    require_list,
    require_object,
)

CONTACTS_READONLY_SCOPE = "https://www.googleapis.com/auth/contacts.readonly"
CONTACTS_READ_MASK = "names,emailAddresses,phoneNumbers,organizations,metadata"
CONTACTS_MAX_PAGE_SIZE = 30


def _contact_from_payload(payload: object) -> Contact:
    data = require_object(payload, "Google Contact normalization")

    def first_mapping(value: object) -> dict[str, Any]:
        if not isinstance(value, list):
            return {}
        for item in value:
            if isinstance(item, dict):
                return item
        return {}

    name = first_mapping(data.get("names"))
    raw_emails = data.get("emailAddresses", [])
    raw_phones = data.get("phoneNumbers", [])
    raw_organizations = data.get("organizations", [])
    try:
        emails: list[ContactEmail] = []
        if isinstance(raw_emails, list):
            for item in raw_emails:
                if not isinstance(item, dict):
                    continue
                value = item.get("value")
                if not isinstance(value, str) or not value.strip():
                    continue
                item_type = item.get("type")
                emails.append(
                    ContactEmail(
                        value=value,
                        type=item_type if isinstance(item_type, str) else None,
                    )
                )

        phones: list[ContactPhone] = []
        if isinstance(raw_phones, list):
            for item in raw_phones:
                if not isinstance(item, dict):
                    continue
                value = item.get("value")
                if not isinstance(value, str) or not value.strip():
                    continue
                item_type = item.get("type")
                phones.append(
                    ContactPhone(
                        value=value,
                        type=item_type if isinstance(item_type, str) else None,
                    )
                )

        organizations: list[str] = []
        if isinstance(raw_organizations, list):
            for item in raw_organizations:
                if not isinstance(item, dict):
                    continue
                organization_name = item.get("name")
                if isinstance(organization_name, str) and organization_name.strip():
                    organizations.append(organization_name.strip())

        resource_name = data.get("resourceName")
        if not isinstance(resource_name, str) or not resource_name.strip():
            raise ValueError("Contact resource name is missing.")
        return Contact(
            resource_name=resource_name,
            etag=data.get("etag") if isinstance(data.get("etag"), str) else None,
            display_name=name.get("displayName")
            if isinstance(name.get("displayName"), str)
            else None,
            given_name=name.get("givenName") if isinstance(name.get("givenName"), str) else None,
            family_name=name.get("familyName") if isinstance(name.get("familyName"), str) else None,
            emails=emails,
            phones=phones,
            organizations=organizations,
        )
    except (PydanticValidationError, TypeError, ValueError) as exc:
        raise ExternalServiceError(
            "Google returned an invalid Contact.", service_name="contacts"
        ) from exc


def _validate_resource_name(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or not normalized.startswith("people/")
        or "?" in normalized
        or "#" in normalized
        or ".." in normalized
    ):
        raise DomainValidationError("Invalid Google Contact resource name.")
    return normalized


def _validate_page_size(value: int) -> int:
    if not 1 <= value <= CONTACTS_MAX_PAGE_SIZE:
        raise DomainValidationError(
            "Contacts page size must be between 1 and 30.",
            details={"page_size": value},
        )
    return value


class ContactsAdapter(GoogleResourceAdapter):
    """Typed People API adapter using the common authorized Google client."""

    required_scope = CONTACTS_READONLY_SCOPE

    async def search(
        self,
        query: str,
        *,
        page_size: int = 30,
        page_token: str | None = None,
    ) -> ContactPage:
        """Search contacts and preserve the People API continuation token."""
        normalized_query = query.strip()
        if not normalized_query:
            raise DomainValidationError("A non-blank contact search query is required.")
        params: dict[str, Any] = {
            "query": normalized_query,
            "pageSize": _validate_page_size(page_size),
            "readMask": CONTACTS_READ_MASK,
        }
        if page_token and page_token.strip():
            params["pageToken"] = page_token.strip()
        payload = await self._request_json(
            "GET",
            "/v1/people:searchContacts",
            operation="Google Contact search",
            params=params,
        )
        data = require_object(payload, "Google Contact search")
        raw_results = require_list(data, "results", "Google Contact search")
        contacts: list[Contact] = []
        for result in raw_results:
            result_data = require_object(result, "Google Contact search")
            person = result_data.get("person")
            if person is None:
                continue
            contacts.append(_contact_from_payload(person))
        return ContactPage(
            items=self._deduplicate(contacts),
            next_page_token=data.get("nextPageToken")
            if isinstance(data.get("nextPageToken"), str)
            else None,
            result_size_estimate=(
                data.get("totalItems") if isinstance(data.get("totalItems"), int) else None
            ),
        )

    async def get(self, resource_name: str) -> Contact:
        """Fetch one Contact by its stable People resource name."""
        identifier = _validate_resource_name(resource_name)
        payload = await self._request_json(
            "GET",
            f"/v1/{quote(identifier, safe='/')}",
            operation="Google Contact lookup",
            params={"personFields": CONTACTS_READ_MASK},
        )
        return _contact_from_payload(payload)

    @staticmethod
    def _deduplicate(contacts: list[Contact]) -> list[Contact]:
        seen: set[str] = set()
        result: list[Contact] = []
        for contact in contacts:
            if contact.resource_name in seen:
                continue
            seen.add(contact.resource_name)
            result.append(contact)
        return result


__all__ = ["CONTACTS_READONLY_SCOPE", "ContactsAdapter"]
