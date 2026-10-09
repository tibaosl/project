"""判斷使用者用什麼語言發問（法規問答的回答、建議問題都要跟著使用者的語言）。"""


def has_han(text: str) -> bool:
    return any(0x3400 <= ord(ch) <= 0x9FFF for ch in text)


def asked_in_english(text: str) -> bool:
    """問題裡沒有中文字、有英文字母，就當成用英文問。"""
    return not has_han(text) and any("a" <= ch.lower() <= "z" for ch in text)
