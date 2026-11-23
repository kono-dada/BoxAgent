"""
Temporal Parser Module

Handles robust date/time extraction and normalization for Jev-Mem.
Includes parsing of relative dates (yesterday, last week), absolute dates,
and conversion to appropriate timestamps.
"""

import re
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

class TemporalParser:
    """
    Handles temporal parsing and date extraction from text.
    """

    def __init__(self):
        """Initialize the temporal parser with common patterns."""
        # Common relative date patterns
        self.relative_patterns = {
            'yesterday': lambda base: base - timedelta(days=1),
            'tomorrow': lambda base: base + timedelta(days=1),
            'today': lambda base: base,
            'last week': lambda base: base - timedelta(weeks=1),
            'week before': lambda base: base - timedelta(weeks=1),
            'week ago': lambda base: base - timedelta(weeks=1),
            'next week': lambda base: base + timedelta(weeks=1),
            'last month': lambda base: base - timedelta(days=30),
            'month ago': lambda base: base - timedelta(days=30),
            'next month': lambda base: base + timedelta(days=30),
            'last year': lambda base: base - timedelta(days=365),
            'next year': lambda base: base + timedelta(days=365),
        }

        # Day-specific patterns (e.g., "last Monday", "Sunday before")
        self.weekday_names = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6
        }

    def parse_session_timestamp(self, date_str: str) -> datetime:
        """
        Parse a session timestamp from various formats.

        Args:
            date_str: Date string to parse

        Returns:
            Parsed datetime object
        """
        if not date_str:
            return datetime.now()

        try:
            # Handle "1:56 pm on 8 May, 2023" format (LoComo dataset specific)
            if "on" in date_str:
                parts = date_str.split(" on ")
                if len(parts) == 2:
                    date_part = parts[1].strip().replace(",", "")
                    # Parse "8 May 2023"
                    return datetime.strptime(date_part, "%d %B %Y")

            # Try standard formats
            formats = [
                "%Y%m%d%H%M",  # YYYYMMDDHHmm
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%d/%m/%Y",
                "%m/%d/%Y",
                "%d %B %Y",
                "%B %d, %Y",
                "%Y%m%d"
            ]

            for fmt in formats:
                try:
                    return datetime.strptime(date_str, fmt)
                except:
                    continue

        except Exception as e:
            logger.warning(f"Failed to parse date '{date_str}': {e}")

        return datetime.now()

    def extract_temporal_reference(self, text: str, base_timestamp: datetime) -> Optional[datetime]:
        """
        Extract temporal reference from text and convert to absolute timestamp.

        Args:
            text: Text containing temporal reference
            base_timestamp: Reference timestamp for relative dates

        Returns:
            Extracted datetime or None
        """
        text_lower = text.lower()

        # Check for relative date patterns
        for pattern, transform in self.relative_patterns.items():
            if pattern in text_lower:
                return transform(base_timestamp)

        # Check for weekday references (e.g., "last Monday", "Sunday before")
        weekday_match = self._extract_weekday_reference(text_lower, base_timestamp)
        if weekday_match:
            return weekday_match

        # Check for absolute date patterns
        absolute_date = self._extract_absolute_date(text)
        if absolute_date:
            return absolute_date

        return None

    def _extract_weekday_reference(self, text: str, base_timestamp: datetime) -> Optional[datetime]:
        """
        Extract weekday-based temporal references.

        Args:
            text: Text in lowercase
            base_timestamp: Reference timestamp

        Returns:
            Extracted datetime or None
        """
        # Pattern: "last/previous [weekday]"
        for weekday, day_num in self.weekday_names.items():
            patterns = [
                f"last {weekday}",
                f"previous {weekday}",
                f"{weekday} before",
                f"{weekday} prior"
            ]

            for pattern in patterns:
                if pattern in text:
                    # Calculate days back to the specified weekday
                    current_day = base_timestamp.weekday()
                    days_back = (current_day - day_num) % 7
                    if days_back == 0:
                        days_back = 7  # Go to previous week's same day
                    return base_timestamp - timedelta(days=days_back)

        # Pattern: "next [weekday]"
        for weekday, day_num in self.weekday_names.items():
            if f"next {weekday}" in text:
                current_day = base_timestamp.weekday()
                days_forward = (day_num - current_day) % 7
                if days_forward == 0:
                    days_forward = 7  # Go to next week's same day
                return base_timestamp + timedelta(days=days_forward)

        return None

    def _extract_absolute_date(self, text: str) -> Optional[datetime]:
        """
        Extract absolute date from text.

        Args:
            text: Text containing date

        Returns:
            Extracted datetime or None
        """
        # Pattern: "DD Month YYYY" (e.g., "8 May 2023")
        match = re.search(r'\b(\d{1,2})\s+(\w+)\s+(\d{4})\b', text)
        if match:
            try:
                date_str = f"{match.group(1)} {match.group(2)} {match.group(3)}"
                return datetime.strptime(date_str, "%d %B %Y")
            except:
                pass

        # Pattern: "Month DD, YYYY" (e.g., "May 8, 2023")
        match = re.search(r'\b(\w+)\s+(\d{1,2}),?\s+(\d{4})\b', text)
        if match:
            try:
                date_str = f"{match.group(1)} {match.group(2)} {match.group(3)}"
                return datetime.strptime(date_str, "%B %d %Y")
            except:
                pass

        # Pattern: "Month YYYY" (e.g., "June 2023")
        match = re.search(r'\b(\w+)\s+(\d{4})\b', text)
        if match:
            try:
                return datetime.strptime(f"{match.group(1)} {match.group(2)}", "%B %Y")
            except:
                pass

        # Pattern: Year only (e.g., "2022")
        match = re.search(r'\b(20\d{2})\b', text)
        if match:
            try:
                return datetime(int(match.group(1)), 1, 1)
            except:
                pass

        return None

    def extract_all_dates(self, text: str, base_timestamp: datetime) -> List[Tuple[str, datetime]]:
        """
        Extract all date mentions from text.

        Args:
            text: Text to analyze
            base_timestamp: Reference timestamp

        Returns:
            List of (date_string, datetime) tuples
        """
        dates = []
        text_lower = text.lower()

        # Extract relative dates
        for pattern in self.relative_patterns.keys():
            if pattern in text_lower:
                date_obj = self.relative_patterns[pattern](base_timestamp)
                dates.append((pattern, date_obj))

        # Extract absolute dates
        # DD Month YYYY
        for match in re.finditer(r'\b(\d{1,2})\s+(\w+)\s+(\d{4})\b', text):
            try:
                date_str = match.group(0)
                date_obj = datetime.strptime(date_str, "%d %B %Y")
                dates.append((date_str, date_obj))
            except:
                pass

        # Month DD, YYYY
        for match in re.finditer(r'\b(\w+)\s+(\d{1,2}),?\s+(\d{4})\b', text):
            try:
                date_str = f"{match.group(1)} {match.group(2)} {match.group(3)}"
                date_obj = datetime.strptime(date_str, "%B %d %Y")
                dates.append((match.group(0), date_obj))
            except:
                pass

        return dates

    def describe_references(self, text: str, base_timestamp: datetime) -> List[dict]:
        """Anchor relative expressions without inventing finer event precision.

        These are text annotations, not replacements for observation timestamps.
        In particular a week, weekend, month or year is never an exact day.
        """
        if base_timestamp is None:
            return []
        base = base_timestamp
        anchor = self.normalize_date_format(base)
        numbers = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
                   'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
        weekdays = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
        pattern = re.compile(
            r'\b(?:yesterday|today|tomorrow|last night|'
            r'(?:last|this|next|this past)\s+(?:weekend|week|month|year)|'
            r'(?:last|next)\s+(?:mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|'
            r'thu(?:rs(?:day)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)|'
            r'(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+'
            r'(?:days?|weeks?|weekends?|months?|years?)\s+ago)\b', re.I)
        references = []
        for match in pattern.finditer(text):
            phrase = match.group().lower()
            normalized = precision = None
            day_offset = {'yesterday': -1, 'last night': -1, 'today': 0, 'tomorrow': 1}.get(phrase)
            if day_offset is not None:
                normalized = self.normalize_date_format(base + timedelta(days=day_offset))
                precision = 'day'
            elif phrase.split()[-1] != 'month' and phrase.split()[-1][:3] in weekdays:
                weekday = weekdays[phrase.split()[-1][:3]]
                forward = phrase.startswith('next ')
                delta = ((weekday - base.weekday()) if forward else (base.weekday() - weekday)) % 7 or 7
                date = base + timedelta(days=delta if forward else -delta)
                normalized = self.normalize_date_format(date)
                precision = 'day'
            else:
                parts = phrase.split()
                count = 1
                direction = -1
                if parts[-1] == 'ago':
                    count = int(parts[0]) if parts[0].isdigit() else numbers[parts[0]]
                    unit = parts[-2].rstrip('s')
                else:
                    unit = parts[-1]
                    direction = 1 if parts[0] == 'next' else 0 if phrase.startswith('this ') and parts[1] != 'past' else -1
                if unit == 'day':
                    normalized = self.normalize_date_format(base + timedelta(days=direction * count))
                    precision = 'day'
                elif unit == 'year':
                    year = base.year + direction * count
                    if 1 <= year <= 9999:
                        normalized, precision = str(year), 'year'
                elif unit == 'month':
                    year, month = divmod(base.year * 12 + base.month - 1 + direction * count, 12)
                    if 1 <= year <= 9999:
                        normalized, precision = datetime(year, month + 1, 1).strftime('%B %Y'), 'month'
                elif unit in ('week', 'weekend'):
                    if direction == 0:
                        normalized = f'The {unit} of {anchor}'
                    else:
                        label = f'The {unit}' if count == 1 else f'{count} {unit}s'
                        normalized = f'{label} {"before" if direction < 0 else "after"} {anchor}'
                    precision = unit
            if normalized:
                references.append({'original': match.group(), 'normalized': normalized,
                                   'precision': precision, 'anchor_date': base.date().isoformat()})
        return references

    def normalize_date_format(self, date: datetime, context: str = "") -> str:
        """
        Normalize date to standard format based on context.

        Args:
            date: Datetime to format
            context: Optional context to determine format

        Returns:
            Formatted date string
        """
        # Remove leading zeros from day
        day = date.day
        month_name = date.strftime("%B")
        year = date.year

        # Standard format: "D Month YYYY" (e.g., "7 May 2023")
        return f"{day} {month_name} {year}"

    def calculate_duration(self, start: datetime, end: datetime,
                          include_ago: bool = False) -> str:
        """
        Calculate duration between two dates in human-readable format.

        Args:
            start: Start datetime
            end: End datetime
            include_ago: Whether to append "ago" to the duration

        Returns:
            Duration string (e.g., "3 days ago", "2 months")
        """
        delta = end - start
        days = abs(delta.days)

        if days == 0:
            return "today"
        elif days == 1:
            result = "1 day"
        elif days < 7:
            result = f"{days} days"
        elif days < 30:
            weeks = days // 7
            result = f"{weeks} week{'s' if weeks > 1 else ''}"
        elif days < 365:
            months = days // 30
            result = f"{months} month{'s' if months > 1 else ''}"
        else:
            years = days // 365
            result = f"{years} year{'s' if years > 1 else ''}"

        if include_ago and delta.days > 0:
            result += " ago"

        return result

    def is_temporal_question(self, question: str) -> bool:
        """
        Check if a question is asking about time/dates.

        Args:
            question: Question text

        Returns:
            True if temporal question
        """
        temporal_keywords = [
            'when', 'what time', 'what date', 'which day',
            'how long', 'how many days', 'how many weeks',
            'how many months', 'how many years', 'what year',
            'what month', 'timeline', 'schedule', 'duration'
        ]

        question_lower = question.lower()
        return any(keyword in question_lower for keyword in temporal_keywords)

    def extract_time_constraints(self, query: str, reference_date: datetime) -> dict:
        """
        Extract time constraints from a query for filtering.

        Args:
            query: Query text
            reference_date: Reference date for relative calculations

        Returns:
            Dict with 'start_date' and 'end_date' if found
        """
        constraints = {}
        query_lower = query.lower()

        # Check for specific time ranges
        if "last week" in query_lower:
            constraints['start_date'] = reference_date - timedelta(weeks=1)
            constraints['end_date'] = reference_date
        elif "last month" in query_lower:
            constraints['start_date'] = reference_date - timedelta(days=30)
            constraints['end_date'] = reference_date
        elif "last year" in query_lower:
            constraints['start_date'] = reference_date - timedelta(days=365)
            constraints['end_date'] = reference_date
        elif "yesterday" in query_lower:
            constraints['start_date'] = reference_date - timedelta(days=1)
            constraints['end_date'] = reference_date - timedelta(days=1)

        # Extract specific dates mentioned
        dates = self.extract_all_dates(query, reference_date)
        if dates:
            # Use the earliest and latest dates as constraints
            date_objs = [d[1] for d in dates]
            constraints['start_date'] = min(date_objs)
            constraints['end_date'] = max(date_objs)

        return constraints
