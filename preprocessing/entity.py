from config import KNOWN_PERSON_NAMES


def extract_person_names(text: str):
    names = set()
    for known_name in KNOWN_PERSON_NAMES:
        if known_name in text:
            names.add(known_name)
    return list(names)
