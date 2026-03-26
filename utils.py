def capitalize_words(text: str) -> str:
    return " ".join(word.capitalize() for word in text.split())


def format_message(template: str, **kwargs: str) -> str:
    return template.format(**kwargs)
