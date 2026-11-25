"""Small, sourced teaching notes; patch-dependent numbers belong in live catalogs."""

from boxagent.domain.proactivity.lol_knowledge import LolKnowledgeEntry

_SOURCE = "https://www.leagueoflegends.com/en-us/how-to-play/"
_BASICS: dict[str, tuple[tuple[str, ...], str]] = {
    "胜利条件": (
        ("水晶", "基地", "高地", "怎么赢"),
        "召唤师峡谷的胜利目标是摧毁敌方主基地。高地水晶与主基地是不同建筑，"
        "摧毁高地水晶不代表对局结束。",
    ),
    "位置分工": (
        ("位置", "分路", "上单", "打野", "中单", "下路", "辅助"),
        "常见分工为上路、打野、中路、下路输出和辅助。位置描述分工，"
        "不能仅凭位置断定某个英雄本局的伤害类型或装备。",
    ),
    "经济与等级": (
        ("补刀", "经验", "经济", "等级", "金币"),
        "金币用于购买装备，经验用于升级并强化技能和基础属性。"
        "教学时同时关注经济与等级，不仅比较击杀数。具体收益按当前版本查询。",
    ),
    "防御塔与视野": (
        ("防御塔", "推塔", "越塔", "视野", "战争迷雾"),
        "防御塔会攻击敌方小兵和英雄，并为己方提供附近视野。"
        "推进防御塔时利用己方兵线，评估交战风险。己方看不到的区域不能推断为安全。",
    ),
    "野区目标": (
        ("野区", "大龙", "小龙", "纳什男爵", "史诗野怪"),
        "野区有中立野怪。纳什男爵和元素亚龙等目标能提供团队增益，"
        "决策需要结合兵线、视野和队友位置。增益数值与刷新规则随版本变化，需另查。",
    ),
    "技能与召唤师技能": (
        ("技能", "被动", "召唤师技能", "闪现", "惩戒", "传送"),
        "多数英雄有被动和 Q、W、E、R 技能，另可选择召唤师技能。"
        "英雄技能与召唤师技能分别查询；不能把两者的冷却或效果混用。",
    ),
}


def lookup_basic(name: str) -> LolKnowledgeEntry | None:
    key = name.strip().casefold()
    matches = [
        (title, description)
        for title, (aliases, description) in _BASICS.items()
        if key in (title, *aliases)
    ]
    if len(matches) != 1:
        return None
    title, description = matches[0]
    return LolKnowledgeEntry(
        title,
        {"说明": description},
        "基础教学整理 2026-11-23",
        "zh_CN",
        _SOURCE,
        "Riot 新手指南与本地教学整理",
    )
