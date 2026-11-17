"""Small game-reference policies; mechanism claims still come from evidence."""

import re


def game_reference_question(question: str, *, game: str = "", target: str = "",
                            known_text: str = "", has_visual_context: bool = False) -> str | None:
    """Stop unresolved deictic game searches before a paid provider request."""
    conflict = boss_name_conflict(question)
    if conflict:
        return conflict
    deictic = re.search(r"这(?:个|只)?\s*(?:boss|怪|关卡|角色)", question, re.IGNORECASE)
    if not deictic:
        return None
    known = (question + "\n" + known_text).casefold().replace("鸣朝", "鸣潮")
    named = bool(game.strip() and target.strip() and game.casefold() in known and target.casefold() in known)
    if not named and not has_visual_context:
        return "你说的是哪款游戏、哪个Boss？"
    return None


def boss_name_conflict(question: str) -> str | None:
    if "无冠者" in question and "周本" in question and "无妄者" not in question:
        return "你说的是无冠者，还是周本无妄者？"
    return None


def game_clarification_reference(question: str) -> str:
    conflict = boss_name_conflict(question)
    if not conflict:
        return ""
    return ("宿主实体检查：本轮游戏敌人名称与形态需要确认，尚未发起检索。"
            "只用一句话问用户：" + conflict + "不要说开始搜索，不调用工具，确认以后再查。\n")


def preferred_game_sites(query: str) -> tuple[str, ...]:
    """The first supported game prefers its publisher-hosted wiki and game wiki."""
    if "鸣潮" in query or "鸣朝" in query or "wuthering waves" in query.casefold():
        return ("wiki.kurobbs.com/mc/", "wiki.biligame.com/wutheringwaves/")
    return ()
