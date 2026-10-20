"""Compile a :class:`MetadataFilterSpec` into parameterised SQLAlchemy Core.

User text only ever reaches the database as bound parameters. Visibility is
always enforced through ``documents``: active version + owner scope
(``user_id = requester OR user_id IS NULL``), mirroring ``retrieval.sql``.
"""

from __future__ import annotations

import re

from sqlalchemy import Select, and_, func, or_, select

from app.core.text import unaccent_vietnamese
from app.domain.models.retrieval.metadata_filter import MetadataFilterSpec, MetadataOperation
from app.infrastructure.db.models import Document, DocumentProfile

ROW_COLUMNS = (
    "document_id",
    "title",
    "doc_category",
    "doc_subtype",
    "doc_number",
    "doc_date",
    "issuer",
    "file_name",
)


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _conditions(spec: MetadataFilterSpec, requester_id: str | None) -> list:
    conds = [Document.is_active.is_(True)]
    if requester_id is None:
        conds.append(Document.user_id.is_(None))
    else:
        conds.append(or_(Document.user_id == requester_id, Document.user_id.is_(None)))
    if spec.categories:
        conds.append(DocumentProfile.doc_category.in_(spec.categories))
    if spec.subtypes:
        conds.append(DocumentProfile.doc_subtype.in_(spec.subtypes))
    if spec.date_from:
        conds.append(DocumentProfile.doc_date >= spec.date_from)
    if spec.date_to:
        conds.append(DocumentProfile.doc_date <= spec.date_to)
    if spec.doc_number:
        norm = unaccent_vietnamese(re.sub(r"\s+", "", spec.doc_number)).upper()
        if norm.isdigit():
            # "1119" matches 1119/QĐ-…, 1119-QĐ/… but not 11190/…
            conds.append(
                or_(
                    DocumentProfile.doc_number_norm.like(f"{norm}/%"),
                    DocumentProfile.doc_number_norm.like(f"{norm}-%"),
                    DocumentProfile.doc_number_norm == norm,
                )
            )
        else:
            conds.append(DocumentProfile.doc_number_norm == norm)
    if spec.issuer:
        pattern = f"%{_like_escape(unaccent_vietnamese(spec.issuer))}%"
        conds.append(DocumentProfile.issuer_norm.like(pattern, escape="\\"))
    if spec.person:
        pattern = f"%{_like_escape(unaccent_vietnamese(spec.person))}%"
        conds.append(DocumentProfile.entity_names_norm.like(pattern, escape="\\"))
    return conds


def _base(spec: MetadataFilterSpec, requester_id: str | None) -> Select:
    return (
        select(
            Document.id.label("document_id"),
            func.coalesce(DocumentProfile.title, Document.title).label("title"),
            DocumentProfile.doc_category,
            DocumentProfile.doc_subtype,
            DocumentProfile.doc_number,
            DocumentProfile.doc_date,
            DocumentProfile.issuer,
            Document.title.label("file_name"),
        )
        .join(DocumentProfile, DocumentProfile.document_id == Document.id)
        .where(and_(*_conditions(spec, requester_id)))
    )


def compile_rows(spec: MetadataFilterSpec, requester_id: str | None) -> Select:
    stmt = _base(spec, requester_id)
    # Undated documents last; newest first for both "list" and "latest".
    stmt = stmt.order_by(
        DocumentProfile.doc_date.is_(None), DocumentProfile.doc_date.desc(), Document.title
    )
    limit = 1 if spec.operation is MetadataOperation.LATEST else spec.limit
    return stmt.limit(limit)


def compile_count(spec: MetadataFilterSpec, requester_id: str | None) -> Select:
    return (
        select(func.count())
        .select_from(Document)
        .join(DocumentProfile, DocumentProfile.document_id == Document.id)
        .where(and_(*_conditions(spec, requester_id)))
    )
