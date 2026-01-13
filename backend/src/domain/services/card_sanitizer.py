"""
Card Sanitizer.

Utilities for sanitizing Anki card content for TTS (Text-to-Speech) output.
Handles HTML tags, cloze deletions, LaTeX, and other formatting.
"""

import re
from html import unescape

# =============================================================================
# Pre-compiled Regex Patterns (Performance Optimization)
# =============================================================================

# HTML and entities
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Cloze patterns
_CLOZE_ANSWER_RE = re.compile(r"\{\{c\d+::(.*?)(?:::.*?)?\}\}")
_CLOZE_BLANK_RE = re.compile(r"\{\{c\d+::.*?(?:::.*?)?\}\}")

# LaTeX delimiters
_DISPLAY_MATH_RE = re.compile(r"\$\$(.*?)\$\$", re.DOTALL)
_INLINE_MATH_RE = re.compile(r"\$(.*?)\$")
_BRACKET_MATH_RE = re.compile(r"\\\[(.*?)\\\]", re.DOTALL)
_PAREN_MATH_RE = re.compile(r"\\\((.*?)\\\)")

# LaTeX commands
_LATEX_CMD_RE = re.compile(r"\\(?:textbf|textit|emph|text)\{(.*?)\}")
_FRAC_RE = re.compile(r"\\frac\{(.*?)\}\{(.*?)\}")
_SQRT_RE = re.compile(r"\\sqrt\{(.*?)\}")
_SUPERSCRIPT_RE = re.compile(r"\^{?(\d+)}?")

# Greek letters
_GREEK_PATTERNS = [
    (re.compile(r"\\alpha\b"), "alpha"),
    (re.compile(r"\\beta\b"), "beta"),
    (re.compile(r"\\gamma\b"), "gamma"),
    (re.compile(r"\\delta\b"), "delta"),
    (re.compile(r"\\epsilon\b"), "epsilon"),
    (re.compile(r"\\theta\b"), "theta"),
    (re.compile(r"\\lambda\b"), "lambda"),
    (re.compile(r"\\mu\b"), "mu"),
    (re.compile(r"\\pi\b"), "pi"),
    (re.compile(r"\\sigma\b"), "sigma"),
    (re.compile(r"\\omega\b"), "omega"),
]

# Math symbols
_MATH_SYMBOLS = [
    (re.compile(r"\\sum\b"), "sum of"),
    (re.compile(r"\\int\b"), "integral of"),
    (re.compile(r"\\infty\b"), "infinity"),
    (re.compile(r"\\pm\b"), "plus or minus"),
    (re.compile(r"\\times\b"), "times"),
    (re.compile(r"\\div\b"), "divided by"),
    (re.compile(r"\\neq\b"), "not equal to"),
    (re.compile(r"\\leq\b"), "less than or equal to"),
    (re.compile(r"\\geq\b"), "greater than or equal to"),
    (re.compile(r"\\approx\b"), "approximately"),
]

# Whitespace normalization
_WHITESPACE_RE = re.compile(r"\s+")


# =============================================================================
# Core Sanitization Functions
# =============================================================================


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = _HTML_TAG_RE.sub("", text)
    return unescape(text)


def _convert_latex_to_spoken(text: str) -> str:
    """Convert LaTeX notation to spoken equivalents."""
    # Remove LaTeX delimiters first (keep content)
    text = _DISPLAY_MATH_RE.sub(r"\1", text)
    text = _INLINE_MATH_RE.sub(r"\1", text)
    text = _BRACKET_MATH_RE.sub(r"\1", text)
    text = _PAREN_MATH_RE.sub(r"\1", text)

    # Remove text formatting commands (keep content)
    text = _LATEX_CMD_RE.sub(r"\1", text)

    # Convert mathematical constructs to spoken form
    text = _FRAC_RE.sub(r"\1 over \2", text)
    text = _SQRT_RE.sub(r"square root of \1", text)

    # Handle superscripts (common cases first)
    text = text.replace("^2", " squared")
    text = text.replace("^3", " cubed")
    text = _SUPERSCRIPT_RE.sub(r" to the power of \1", text)

    # Convert Greek letters
    for pattern, spoken in _GREEK_PATTERNS:
        text = pattern.sub(spoken, text)

    # Convert math symbols
    for pattern, spoken in _MATH_SYMBOLS:
        text = pattern.sub(spoken, text)

    return text


def _normalize_whitespace(text: str) -> str:
    """Normalize whitespace and strip."""
    return _WHITESPACE_RE.sub(" ", text).strip()


# =============================================================================
# Public API
# =============================================================================


def sanitize_question_for_tts(text: str) -> str:
    """Sanitize question text for TTS - hide cloze answers.

    Replaces cloze deletions with 'blank' so the answer isn't spoiled.

    Args:
        text: Raw card front content (may contain HTML, cloze, LaTeX)

    Returns:
        Clean text with cloze answers hidden
    """
    if not text:
        return ""

    text = _strip_html(text)
    text = _CLOZE_BLANK_RE.sub("blank", text)  # Hide answer
    text = _convert_latex_to_spoken(text)
    return _normalize_whitespace(text)


def sanitize_answer_for_tts(text: str) -> str:
    """Sanitize answer text for TTS - reveal cloze answers.

    Extracts the answer text from cloze deletions.

    Args:
        text: Raw card back content (may contain HTML, cloze, LaTeX)

    Returns:
        Clean text with cloze answers revealed
    """
    if not text:
        return ""

    text = _strip_html(text)
    text = _CLOZE_ANSWER_RE.sub(r"\1", text)  # Reveal answer
    text = _convert_latex_to_spoken(text)
    return _normalize_whitespace(text)


def sanitize_for_tts(text: str) -> str:
    """Sanitize text for TTS output (alias for answer sanitization).

    This is the original function - kept for backward compatibility.
    For questions, use sanitize_question_for_tts() to hide cloze answers.

    Args:
        text: Raw card content (may contain HTML, cloze, LaTeX)

    Returns:
        Clean text suitable for TTS
    """
    return sanitize_answer_for_tts(text)


def is_readable_card(card: dict) -> bool:
    """Check if card has content that can be read aloud.

    Returns False for image-only cards or cards with minimal text.

    Args:
        card: Card dictionary with 'front' key

    Returns:
        True if card has readable content
    """
    front = sanitize_for_tts(card.get("front", ""))
    # Minimum 3 characters to be considered readable
    return len(front) >= 3


def generate_fallback_hint(answer: str, hint_level: int) -> str:
    """Generate fallback hints when LLM hint generation fails.

    This is a simpler approach that reveals parts of the answer progressively.
    Used as fallback when the LLM-based HintService is unavailable.

    Args:
        answer: The full expected answer
        hint_level: 0=first sentence/phrase, 1=more content, 2+=full answer

    Returns:
        Hint string for TTS
    """
    # Clean the answer first
    clean_answer = sanitize_for_tts(answer)
    if not clean_answer:
        return "I don't have a hint for this one."

    if hint_level == 0:
        # First hint: Give the first sentence or first ~60 chars
        if "." in clean_answer:
            first_sentence = clean_answer.split(".")[0].strip()
            if len(first_sentence) > 10:
                return f"Here's a hint: {first_sentence}..."
        # Fallback: first ~60 characters at word boundary
        if len(clean_answer) > 60:
            cutoff = clean_answer[:60].rfind(" ")
            if cutoff > 20:
                return f"Here's a hint: {clean_answer[:cutoff]}..."
        return f"Here's a hint: {clean_answer[:min(60, len(clean_answer))]}..."

    elif hint_level == 1:
        # Second hint: Give more content - first half of answer
        half = len(clean_answer) // 2
        # Find word boundary
        cutoff = clean_answer[:half].rfind(" ")
        if cutoff > 20:
            return f"More detail: {clean_answer[:cutoff]}..."
        return f"More detail: {clean_answer[:half]}..."

    else:
        # Full answer
        return f"The answer is: {clean_answer}"


# Alias for backward compatibility
generate_progressive_hint = generate_fallback_hint
