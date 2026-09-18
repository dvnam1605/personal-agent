"""Declarative rules, compiled patterns, and safety filters for FastTriage (H3 / M9).

Externalizes pattern matching definitions to maintain readability, isolation,
and high performance (<= 10ms execution). Includes bilingual English/Vietnamese
prompt-attack and jailbreak definitions, tightened time signal filters,
and deterministic domain predicates.
"""

from __future__ import annotations

import re

# ----------------------------------------------------------------------
# 1. Prompt Attack, Jailbreak & Destructive Filters (Strict Safety Gate)
# ----------------------------------------------------------------------

# Bilingual Prompt Injection & Jailbreak (English + Vietnamese accented & unaccented)
PROMPT_ATTACK_PATTERN = re.compile(
    r"\b("
    # English patterns
    r"ignore\s+previous\s+instructions|"
    r"system\s+prompt|"
    r"(dump|show|reveal|print|display|leak|get|tell\s+me|give\s+me|expose)\s+(me\s+)?(the\s+|your\s+)?(system\s+)?prompt|"
    r"jailbreak|"
    r"disregard\s+all\s+rules|"
    r"bypass\s+safety|"
    # Vietnamese patterns (unaccented + accented covered via unaccented matching)
    r"bo\s+qua\s+(tat\s+ca\s+|moi\s+)?(huong\s+dan|chi\s+thi|quy\s+tac|lenh)(\s+truoc\s+do)?|"
    r"xuat\s+(toan\s+bo\s+)?(system\s+prompt|loi\s+nhac\s+he\s+thong|cau\s+lenh\s+goc)|"
    r"cho\s+toi\s+biet\s+(system\s+prompt|loi\s+nhac\s+he\s+thong)|"
    r"pha\s+khoa|"
    r"chiem\s+quyen|"
    r"vuot\s+qua\s+kiem\s+duyet|"
    r"pha\s+hoai\s+he\s+thong"
    r")\b",
    re.IGNORECASE,
)

DESTRUCTIVE_COMMAND_PATTERN = re.compile(
    r"("
    r"\bdrop\s+table\b|"
    r"\bdelete\s+from\s+\w+|"
    r"\brm\s+-rf|"
    r"\btruncate\s+table\b|"
    r"\bformat\s+c:?|"
    r"\bxoa\s+bang\b|"
    r"\bxoa\s+toan\s+bo\s+bang\b|"
    r"\bxoa\s+co\s+so\s+du\s+lieu\b|"
    r"\bdrop\s+database\b"
    r")",
    re.IGNORECASE,
)

# Strict definitional inquiry context (ONLY educational queries: "là gì", "nghĩa là gì")
DEFINITIONAL_INQUIRY_PATTERN = re.compile(
    r"\b(la gi|nghia la gi|giai thich( ro| giup)?|dinh nghia)\b",
    re.IGNORECASE,
)

# ----------------------------------------------------------------------
# 2. Calendar Domain Patterns
# ----------------------------------------------------------------------

NON_MEETING_HOP = re.compile(
    r"\b(hop\s+(dong|tac|ly|phap|le|nhat|quy|am|thu|phan)|phu\s+hop|thich\s+hop|tong\s+hop|truong\s+hop|ket\s+hop|tap\s+hop|phoi\s+hop|trung\s+hop)\b",
    re.IGNORECASE,
)

NON_CALENDAR_KE_HOACH = re.compile(
    r"\b(ban\s+ke\s+hoach|ke\s+hoach\s+(tai\s+chinh|ngan\s+sach|thu\s+chi|tiet\s+kiem|khoa\s+hoc|cong\s+tac|phat\s+trien|nam))\b",
    re.IGNORECASE,
)


CALENDAR_CORE = re.compile(
    r"\b(lich|calendar|hop|cuoc hop|meeting|su kien|event|cuoc hen|lich trinh|ke hoach|lich lam viec)\b",
    re.IGNORECASE,
)

CALENDAR_WEEKDAY = re.compile(
    r"\b(thu (hai|ba|nam|sau|bay|may)|chu nhat|t2|t3|t4|t5|t6|t7|cn)\b",
    re.IGNORECASE,
)

CALENDAR_WEDNESDAY = re.compile(
    r"\bthu tu\b",
    re.IGNORECASE,
)

ORDER_PHRASE = re.compile(
    r"\b(theo thu tu|thu tu uu tien|so thu tu|thu tu alphabet|thu tu tang dan|thu tu giam dan|dung thu tu|thu tu cua)\b",
    re.IGNORECASE,
)

CALENDAR_RELATIVE = re.compile(
    r"\b(ngay mai|hom nay|sang mai|chieu nay|chieu mai|toi nay|tuan nay|tuan sau|thang nay|thang sau)\b",
    re.IGNORECASE,
)

CALENDAR_INQUIRY = re.compile(
    r"\b(co gi|co lich|co hop|co hen|co su kien|ke hoach|ranh|co ranh|trong lich|luc may gio|may gio)\b",
    re.IGNORECASE,
)

# Tightened time signals: only matches temporal contexts or time inquiries
TIME_SIGNAL_TIGHT = re.compile(
    r"\b(sang mai|chieu nay|chieu mai|toi nay|ngay mai|tuan nay|tuan sau|thang sau|"
    r"may gio|luc may gio|khi nao|thoi gian nao)\b|"
    r"\b(sang|chieu|toi)\s+(thu\s+(hai|ba|tu|nam|sau|bay)|chu\s+nhat|\d{1,2})|"
    r"\b\d{1,2}\s*(h|gio|am|pm)\b",
    re.IGNORECASE,
)

# ----------------------------------------------------------------------
# 3. Communication Domain Patterns
# ----------------------------------------------------------------------

COMMUNICATION_PATTERN = re.compile(
    r"\b("
    r"email|mail|e-mail|gmail|"
    r"gui\s+thu|soan\s+thu|nhap\s+thu|tra\s+loi\s+thu|hom\s+thu|hop\s+thu|"
    r"doc\s+thu|xem\s+thu|kiem\s+tra\s+thu|check\s+thu|thu\s+moi|thu\s+den|thu\s+di|thu\s+cua|"
    r"thu\s+tu\s+(doi\s+tac|khach\s+hang|cong\s+ty|ban\s+giam\s+doc|phong\s+ban|ben\s+ngoai|nhan\s+vien|sep)|thong\s+diep|"
    r"gui\s+mail|doc\s+mail|doc\s+email|soan\s+email|tra\s+loi\s+email|xoa\s+thu|chuyen\s+tiep\s+thu|"
    r"tin\s+nhan|nhan\s+tin|"
    r"follow\s*up|follow-up|followup"
    r")\b",
    re.IGNORECASE,
)

FOLLOWUP_AFTER_MEETING = re.compile(
    r"\b(follow[\s-]*up|soan\s+follow[\s-]*up|gui\s+thu|gui\s+mail|lien\s+he)\b.*\b(after\s+the\s+meeting|sau\s+cuoc\s+hop|sau\s+hop|with\s+\w+|cho\s+\w+|voi\s+\w+|meeting\s+notes|ghi\s+chu\s+hop|bien\s+ban\s+hop|noi\s+dung\s+hop|notes)\b|"
    r"\b(meeting\s+notes|ghi\s+chu\s+hop|bien\s+ban\s+hop|noi\s+dung\s+hop)\b.*\b(follow[\s-]*up|soan\s+follow[\s-]*up|gui\s+thu|gui\s+mail|lien\s+he)\b",
    re.IGNORECASE,
)

INVITATION_PATTERN = re.compile(
    r"\b(thu\s+moi|email\s+moi|mail\s+moi|soan\s+thu\s+moi|gui\s+thu\s+moi|phan\s+hoi\s+email\s+moi|moi\s+tham\s+du)\b",
    re.IGNORECASE,
)

# ----------------------------------------------------------------------
# 4. Knowledge / Research Domain Patterns
# ----------------------------------------------------------------------


RESEARCH_DOC_PATTERN = re.compile(
    r"\b("
    r"tai lieu|quy dinh|quyet dinh|chinh sach|huong dan|nghien cuu|bao cao|van ban|noi bo|quy che|bieu mau|dieu khoan|"
    r"du toan|dua toan|kinh phi|ngan sach|chi phi|thu chi|tiet kiem|chong lang phi|"
    r"khen thuong|bang khen|thi dua|danh hieu|chien si thi dua|khen|"
    r"dao tao|boi duong|tap huan|nghiep vu|"
    r"khoa hoc|cong nghe|de tai|de an|thong tin khoa hoc|"
    r"nhan su|tien luong|luong|phu cap|bo nhiem|thoi viec|nghi huu|hop dong lao dong|"
    r"phat song|chuong trinh phat thanh|khung phat song|"
    r"chi thi|cong van|thong bao|ke hoach|chuong trinh|"
    r"dai tieng noi|tieng noi viet nam|vov|tong giam doc|pho tong giam doc|trung tam r&d|ban ke hoach|tai chinh|"
    r"qd-tnvn|qd\s*/\s*tnvn|qd\s*\d+"
    r")\b",
    re.IGNORECASE,
)

RESEARCH_LOOKUP_PATTERN = re.compile(
    r"\b("
    r"tim kiem|tim hieu|tra cuu|tra van|"
    r"la bao nhieu|bao nhieu|bao nhieu tien|het bao nhieu|"
    r"nhu the nao|nhu nao|the nao|ra sao|"
    r"ai|nhung ai|ai duoc|don vi nao|tap the nao|ca nhan nao|phong nao|ban nao|"
    r"cho biet|cho toi biet|hoi ve|thong tin ve|noi ve|noi dung ve|quy dinh ve"
    r")\b",
    re.IGNORECASE,
)

TIM_PREFIX_PATTERN = re.compile(r"\btim\s+", re.IGNORECASE)

# ----------------------------------------------------------------------
# 7. Document-topic guard (meeting nouns inside document titles)
# ----------------------------------------------------------------------

# Calendar noise that frequently appears inside a DOCUMENT TITLE or topic
# ("Quy chế tổ chức cuộc họp...", "Kế hoạch chuyển đổi số...") rather than as
# a calendar action.
DOC_TITLE_CALENDAR_NOISE_PATTERN = re.compile(
    r"\b(cuoc hop|hop|meeting|ke hoach)\b", re.IGNORECASE
)

# Read/summarize verbs: the request is about document CONTENT.
DOC_READ_PATTERN = re.compile(
    r"\b("
    r"tim|tra cuu|tra van|tim kiem|tim hieu|"
    r"tom tat|tom luoc|tom gon|tong hop|trich yeu|trich dan|briefing|"
    r"noi dung|doc"
    r")\b",
    re.IGNORECASE,
)

# Duration phrases ("trước 24 giờ/ngày") are deadlines, not calendar time signals.
DOC_DURATION_PATTERN = re.compile(
    r"\b(truoc|sau|trong|toi da|it nhat|toi thieu)\s+\d+\s*(gio|h|phut|giay|ngay|tuan|thang|nam)\b",
    re.IGNORECASE,
)

# ----------------------------------------------------------------------
# 5. Casual / Chit-chat Patterns
# ----------------------------------------------------------------------

CASUAL_PATTERN = re.compile(
    r"\b(xin chao|chao ban|chao|hello|hi|hey|ban la ai|can tro giup|tro giup|help|cam on|thanks|thank you|chuc|tot lanh)\b|"
    r"\b(an gi|uong gi|mon gi|an com|an trua|an toi|an sang|thoi tiet|tam trang|khoe khong|the nao roi|co vui khong)\b",
    re.IGNORECASE,
)

# ----------------------------------------------------------------------
# 6. Stage 2 Lightweight Action Verbs (<= 500ms fallback)
# ----------------------------------------------------------------------

STAGE2_BOOKING = re.compile(
    r"\b(dat cho|dat ban|dat phong|hen gio|xep lich|dat hen|nhac nho|nhac toi)\b",
    re.IGNORECASE,
)

STAGE2_MESSAGING = re.compile(
    r"\b(nhan tin|bao cho|nhan cho|gui loi|phan hoi|reply)\b",
    re.IGNORECASE,
)

STAGE2_LOOKUP = re.compile(
    r"\b(hoi ve|thong tin ve|tim hieu ve|dinh nghia|huong dan su dung|la bao nhieu|nhu the nao|nhu nao|ai duoc)\b",
    re.IGNORECASE,
)
