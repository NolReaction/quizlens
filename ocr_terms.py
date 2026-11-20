"""Document-backed suggestions for rechecking bilingual OCR word images."""
import re
from collections import defaultdict

_WORD = re.compile(r"(?<!\w)[^\W\d_]{4,40}(?!\w)")
# Some shapes (Russian и and г) recur when Latin u and r are read in a
# Cyrillic context. This key proposes candidates; it is never replacement text.
_CYRILLIC_SHAPES = str.maketrans({
    "а": "a", "в": "b", "с": "c", "е": "e", "і": "i", "ј": "j",
    "к": "k", "м": "m", "о": "o", "п": "n", "р": "p", "т": "t",
    "у": "y", "х": "x", "и": "u", "г": "r", "н": "h", "ѕ": "s",
})


def latin_visual_key(token):
    return token.casefold().translate(_CYRILLIC_SHAPES)


class OCRVocabulary:
    def __init__(self, sections):
        known = set()
        for row in sections:
            texts = [row["text"], row.get("question")]
            if isinstance(row.get("options"), (list, tuple)):
                texts.extend(row["options"])
            for text in texts:
                if isinstance(text, str):
                    known.update(word.casefold() for word in _WORD.findall(text))
        self.known = known
        self.latin = {word for word in known if word.isascii() and word.isalpha()}
        self.neighbors = defaultdict(list)
        self.vertical_endings = defaultdict(set)
        for word in self.latin:
            if len(word) >= 6:
                self.neighbors[(word[:2], len(word))].append(word)
            for count in (1, 2):
                if len(word)-count >= 4 and all(c in "il" for c in word[-count:]):
                    self.vertical_endings[word[1:-count]].add(word)

    def ending_candidate(self, token):
        # A pair of vertical stems may look like '!' in a bilingual line
        # ('Веса!' for 'Recall'). The English reread must confirm every letter.
        if token.casefold() in self.known or token.isascii():
            return None
        matches = self.vertical_endings.get(latin_visual_key(token)[1:])
        return next(iter(matches)) if matches and len(matches) == 1 else None

    def candidate(self, token):
        word = token.casefold()
        if not 4 <= len(word) <= 40 or not word.isalpha() or word in self.known:
            return None
        key = latin_visual_key(word)
        if key in self.latin:
            return key
        if not key.isascii() or len(key) < 6:
            return None
        # One substitution in a long technical term, e.g. Acciracy. Only a
        # unique suggestion may trigger a second visual reading.
        candidates = self.neighbors.get((key[:2], len(key)), ())
        matched = None
        for other in candidates:
            if sum(a != b for a, b in zip(key, other)) == 1:
                if matched is not None:
                    return None
                matched = other
        return matched
