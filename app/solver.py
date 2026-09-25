"""共窑排程核心算法。

输入 1~14 件器物（唯一编号、正整数体积、允许烧成温度闭区间）与单窑容量，
输出最少窑次的装窑方案；窑次相同时选择整体字典序最小的方案。

同窑规则：
  * 总体积不超过单窑容量；
  * 各器物温度区间存在公共交集；
  * 烧成温度取交集下界（= 各区间下界的最大值）。

算法：
  1. 枚举全部 2^n 个器物子集，递推每个子集的总体积与温度交集，判定能否成窑。
  2. 位掩码 DP 求每个掩码所需最少窑次 dp[mask]。
  3. 贪心重建字典序最小方案：每一步在剩余器物中，找到包含剩余器物
     最小编号、且取出后剩余部分仍可在剩余窑次内装完的、
     器物编号序列字典序最小的子集作为下一窑。

限窑次接单（select_within_kiln_limit）复用上述子集表与 dp：
枚举全部子集，在 dp[mask] 不超过窑次上限的子集内，按「保留件数最多 →
保留总体积最大 → 保留编号升序列表字典序最小」择优；选中器物的装窑
仍调用 solve，沿用最少窑次与字典序排序。

方案对比（compare_kiln_assignments）：以共同器物编号为范围，把来源与
目标窑次一对一配对（配对数取两边窑次数较小值），使留在对应窑中的共同
器物数最大。权重 w[i][j] = 来源窑 i 与目标窑 j 的共同器物数，问题即
二分图最大权匹配；用「按来源窑序 × 目标窑子集」的位掩码 DP 求最优值，
再按「（来源序号、目标序号）配对序列字典序最小」贪心重建配对。

追加器物（append_items）：原窑及其次序保持不变，新器物可填入剩余容量
与温度区间交集均满足的原窑（同一原窑可填多件，按合并整体判定），或进入
追加窑次。以后缀位掩码 DP 求新增窑次最少的全局方案，再按原窑次序贪心
重建整体字典序最小者；剩余新器物的追加窑次复用 solve。

取消器物（cancel_items）：从原方案删除指定器物后对剩余器物紧凑重排。
第一目标为最少窑次（复用子集表与 dp）；并列时以「取消后仍非空的旧窑与
新窑一对一最优配对（配对数取两边窑次数较小值，被清空的旧窑不参与配对）
后仍留在对应窑的器物数」最多为次目标，配对并列沿用
compare_kiln_assignments 的配对序列字典序规则；仍并列时取整体字典序
最小方案。因删除器物不会破坏成窑可行性，新窑次必不多于非空旧窑数，
故每个新窑都能与一个旧窑配对。算法按非空旧窑序做
「覆盖器物掩码 × 已用窑次 × 窑数 × 留窑增益」的位掩码 DP，每个旧窑可
跳过或与一个可行新窑配对，各增益档分别记录已建窑的最小规范排序键
（向已排序窑列插入新窑对排序键保序），最后取最少窑次层中增益最大者；
新旧窑配对并列时再调用 compare_kiln_assignments 取配对序列字典序最小者。
"""

from __future__ import annotations

from bisect import insort
from collections.abc import Sequence
from dataclasses import dataclass


class CapacityExceededError(ValueError):
    """单件器物体积超过单窑容量，无法烧成。"""


class TargetUnreachableError(ValueError):
    """容量取器物总体积时最少窑次仍超过目标窑次，目标不可达。"""


@dataclass(frozen=True)
class Ware:
    item_id: str
    volume: int
    temp_low: int
    temp_high: int


@dataclass(frozen=True)
class Kiln:
    """一窑：装入的器物编号（升序）、总体积、烧成温度。"""

    item_ids: tuple[str, ...]
    total_volume: int
    firing_temp: int


@dataclass(frozen=True)
class ProductionLimit:
    """限窑次接单结果：保留/延期器物编号（升序）与保留器物的装窑明细。"""

    kept_item_ids: tuple[str, ...]
    delayed_item_ids: tuple[str, ...]
    kilns: tuple[Kiln, ...]


@dataclass(frozen=True)
class PlanComparison:
    """两份方案的比较结果。

    pairs 为（来源窑次序号, 目标窑次序号）配对，按来源序号升序；
    各编号列表均按标准字符串字典序升序。
    """

    pairs: tuple[tuple[int, int], ...]
    stayed_item_ids: tuple[str, ...]        # 留在对应窑的共同器物
    moved_item_ids: tuple[str, ...]         # 换窑的共同器物（含未落在配对窑内者）
    only_in_source_item_ids: tuple[str, ...]
    only_in_target_item_ids: tuple[str, ...]


@dataclass(frozen=True)
class CancelResult:
    """取消器物后的紧凑重排结果。

    kilns 为新方案各窑（窑次序号从 1 开始，窑内编号升序）；
    pairs 为取消后仍非空的（原窑次序号, 新窑次序号）最优配对，
    按原窑次序号升序；被清空的旧窑不参与配对。
    各编号列表均按标准字符串字典序升序。
    """

    canceled_item_ids: tuple[str, ...]
    stayed_item_ids: tuple[str, ...]
    moved_item_ids: tuple[str, ...]
    pairs: tuple[tuple[int, int], ...]
    kilns: tuple[Kiln, ...]


@dataclass(frozen=True)
class _Tables:
    """子集枚举与位掩码 DP 的中间结果（位 i 对应编号升序后的第 i 件）。"""

    n: int
    ids: list[str]
    mask_volume: list[int]
    mask_temp_low: list[int]  # 交集下界（烧成温度）
    valid: bytearray
    dp: list[int]


def _check_item_count(n: int) -> None:
    if n == 0:
        raise ValueError("至少需要一件器物")
    if n > 14:
        raise ValueError("器物数量不得超过 14 件")


def _build_tables(items: list[Ware], capacity: int) -> _Tables:
    """枚举 2^n 个子集，递推可成窑标记、子集总体积/温度交集与最少窑次 dp。

    :raises CapacityExceededError: 存在单件体积超过容量的器物。
    """

    n = len(items)
    _check_item_count(n)

    # 器物按编号字典序升序排列；位 i 对应排序后的第 i 件。
    ordered = sorted(items, key=lambda it: it.item_id)
    ids = [it.item_id for it in ordered]
    volumes = [it.volume for it in ordered]
    low = [it.temp_low for it in ordered]
    high = [it.temp_high for it in ordered]

    oversized = [it.item_id for it in ordered if it.volume > capacity]
    if oversized:
        raise CapacityExceededError(
            f"以下器物单件体积超过单窑容量 {capacity}，无法烧成: "
            + ", ".join(oversized)
        )

    size = 1 << n

    # ---- 1. 每个子集的总体积、温度交集与可行性（低位加一件递推） ----
    valid = bytearray(size)
    mask_volume = [0] * size
    mask_temp_low = [0] * size   # 交集下界（烧成温度）
    mask_temp_high = [0] * size  # 交集上界
    for mask in range(1, size):
        bit = mask & -mask  # 最低位
        i = bit.bit_length() - 1
        prev = mask ^ bit
        total = mask_volume[prev] + volumes[i]
        if prev == 0:
            inter_low, inter_high = low[i], high[i]
            feasible = volumes[i] <= capacity
        else:
            inter_low = max(mask_temp_low[prev], low[i])
            inter_high = min(mask_temp_high[prev], high[i])
            feasible = (
                total <= capacity
                and inter_low <= inter_high
                and bool(valid[prev])
            )
        mask_volume[mask] = total
        mask_temp_low[mask] = inter_low
        mask_temp_high[mask] = inter_high
        if feasible:
            valid[mask] = 1

    # ---- 2. 位掩码 DP：最少窑次 ----
    # dp[0] = 0；dp[mask] = 1 + min(dp[mask ^ g])，g 为 mask 的非空可行子集
    dp = [n + 1] * size
    dp[0] = 0
    for mask in range(1, size):
        best = dp[mask]
        sub = mask
        while sub:
            if valid[sub]:
                candidate = dp[mask ^ sub] + 1
                if candidate < best:
                    best = candidate
            sub = (sub - 1) & mask
        dp[mask] = best

    return _Tables(n, ids, mask_volume, mask_temp_low, valid, dp)


def solve(items: list[Ware], capacity: int) -> list[Kiln]:
    """求最少窑次、字典序最小的装窑方案。

    器物编号按标准字符串字典序排列（如 "1" < "10" < "2"），
    窑内编号升序、窑次间的整体字典序比较均以此为准。

    :raises CapacityExceededError: 存在单件体积超过容量的器物。
    :raises ValueError: 器物列表为空或超过 14 件。
    """

    tables = _build_tables(items, capacity)
    n = tables.n
    ids = tables.ids
    valid = tables.valid
    dp = tables.dp
    mask_volume = tables.mask_volume
    mask_temp_low = tables.mask_temp_low
    size = 1 << n

    # ---- 3. 贪心重建字典序最小方案 ----
    full = size - 1
    allowed_kilns = dp[full]
    left = full
    kilns: list[Kiln] = []

    while left:
        first = (left & -left).bit_length() - 1  # 剩余器物中编号最小者
        rest_bits = left ^ (1 << first)

        # 枚举包含 first 的所有子集，在“取出后剩余器物能在
        # allowed_kilns-1 窑内装完”的可行组中，取器物编号序列字典序最小者。
        best_group = 0
        best_tuple: tuple[str, ...] | None = None
        sub_rest = rest_bits
        while True:
            group = sub_rest | (1 << first)
            if valid[group] and dp[left ^ group] <= allowed_kilns - 1:
                members = tuple(
                    ids[i] for i in range(n) if (group >> i) & 1
                )
                if best_tuple is None or members < best_tuple:
                    best_tuple = members
                    best_group = group
            if sub_rest == 0:
                break
            sub_rest = (sub_rest - 1) & rest_bits

        kilns.append(
            Kiln(
                item_ids=best_tuple,
                total_volume=mask_volume[best_group],
                firing_temp=mask_temp_low[best_group],
            )
        )
        left ^= best_group
        allowed_kilns -= 1

    return kilns


def select_within_kiln_limit(
    items: list[Ware], capacity: int, max_kilns: int
) -> ProductionLimit:
    """限窑次接单：在最多 max_kilns 窑内选择可烧器物。

    使用给定单窑容量与各器物的体积、温度区间，枚举全部 2^n 个器物子集，
    仅考虑最少窑次 ``dp[mask] ≤ max_kilns`` 的子集，按以下优先级择优：
      1. 保留件数最多；
      2. 保留器物总体积最大；
      3. 仍并列时，保留编号升序列表（标准字符串字典序）最小。
    选中后装窑完全复用 solve：实际窑次取保留器物的最少窑次
    （可能少于 max_kilns），方案取整体字典序最小者。

    :raises CapacityExceededError: 存在单件体积超过容量的器物。
    :raises ValueError: 器物列表为空/超过 14 件，或 max_kilns 小于 1。
    """

    n = len(items)
    _check_item_count(n)
    if max_kilns < 1:
        raise ValueError("限窑次至少为 1")

    tables = _build_tables(items, capacity)
    size = 1 << n

    # ---- 在可于 max_kilns 窑内装完的子集中择优 ----
    best_mask = 0
    best_count = 0
    best_volume = 0
    best_ids: tuple[str, ...] | None = None

    for mask in range(1, size):
        if tables.dp[mask] > max_kilns:
            continue
        count = mask.bit_count()
        total = tables.mask_volume[mask]
        if best_ids is not None and (
            count < best_count
            or (count == best_count and total < best_volume)
        ):
            continue
        members = tuple(
            tables.ids[i] for i in range(n) if (mask >> i) & 1
        )
        if (
            best_ids is not None
            and count == best_count
            and total == best_volume
            and members >= best_ids
        ):
            continue
        best_mask = mask
        best_count = count
        best_volume = total
        best_ids = members

    # max_kilns >= 1，且 _build_tables 已排除单件超容器物，
    # 每件器物均可单独成窑，至少能保留一件。
    assert best_ids is not None and best_mask != 0

    # ---- 保留器物的装窑沿用现有最少窑次与方案排序 ----
    by_id = {it.item_id: it for it in items}
    kept_wares = [by_id[i] for i in best_ids]
    kilns = tuple(solve(kept_wares, capacity))

    kept_set = set(best_ids)
    delayed_ids = tuple(i for i in tables.ids if i not in kept_set)

    return ProductionLimit(
        kept_item_ids=best_ids,
        delayed_item_ids=delayed_ids,
        kilns=kilns,
    )


def min_capacity_for_target(
    items: list[Ware], target_kiln_count: int
) -> tuple[int, list[Kiln]]:
    """求使最少窑次不超过 target_kiln_count 的最小单窑容量。

    在 [最大单件体积, 器物总体积] 的整数容量范围内搜索：
    容量不小于最大单件体积时 solve 不会抛出 CapacityExceededError，
    容量达到总体积时体积约束名存实亡（仅剩温区规则）。

    窑次数随容量单调不增——容量放宽只会扩大可成窑子集集合，
    dp 值不会变大——故二分查找最小可行容量；
    每个容量点的判定与方案排序完全复用 solve。

    :returns: (最小容量, 该容量下最少窑次且字典序最小的装窑方案)。
    :raises TargetUnreachableError: 容量取总体积时最少窑次仍超过目标。
    :raises ValueError: 器物列表为空或超过 14 件，或目标窑次小于 1。
    """

    n = len(items)
    if n == 0:
        raise ValueError("至少需要一件器物")
    if n > 14:
        raise ValueError("器物数量不得超过 14 件")
    if target_kiln_count < 1:
        raise ValueError("目标窑次至少为 1")

    lo = max(it.volume for it in items)
    hi = sum(it.volume for it in items)

    # best_kilns 始终保持为 solve(items, hi) 的结果。
    best_kilns = solve(items, hi)
    if len(best_kilns) > target_kiln_count:
        raise TargetUnreachableError(
            f"容量取器物总体积 {hi} 时最少仍需 {len(best_kilns)} 窑，"
            f"超过目标 {target_kiln_count} 窑，目标不可达"
        )

    while lo < hi:
        mid = (lo + hi) // 2
        kilns = solve(items, mid)
        if len(kilns) <= target_kiln_count:
            hi = mid
            best_kilns = kilns
        else:
            lo = mid + 1

    return lo, best_kilns


def compare_kiln_assignments(
    source_kilns: Sequence[Sequence[str]],
    target_kilns: Sequence[Sequence[str]],
) -> PlanComparison:
    """比较两份排程方案，求来源与目标窑次的一对一配对。

    以共同器物编号为范围，配对数取两边窑次数的较小值，使留在对应窑中的
    共同器物数最大；最优解并列时，按来源序号排序的（来源序号、目标序号）
    配对序列取字典序最小。共同器物未落在配对窑内（所在来源窑或目标窑
    未被配对）也算换窑。

    记 w[i][j] 为来源窑 i 与目标窑 j 的共同器物数，则「留窑数最大」即
    二分图最大权匹配。窑次数不超过器物数（≤ 14），用位掩码 DP 求解：
    g[i][tmask] 表示来源窑 i..m 在目标窑已用集合 tmask 下、恰好配满
    pair_count 对时剩余配对最多还能保留的共同器物数（不可行为 -1）；
    再按配对序列字典序最小贪心重建。

    :param source_kilns: 来源方案各窑的器物编号（窑次序号从 1 开始，与列表顺序一致）
    :param target_kilns: 目标方案各窑的器物编号
    :raises ValueError: 任一方案的窑次列表为空。
    """

    m = len(source_kilns)
    k = len(target_kilns)
    if m == 0 or k == 0:
        raise ValueError("每份方案至少包含一个窑次")

    # 器物编号 -> 所在窑次序号（1 起）
    src_of: dict[str, int] = {}
    for idx, kiln in enumerate(source_kilns, start=1):
        for item_id in kiln:
            src_of[item_id] = idx
    tgt_of: dict[str, int] = {}
    for idx, kiln in enumerate(target_kilns, start=1):
        for item_id in kiln:
            tgt_of[item_id] = idx

    common = sorted(set(src_of) & set(tgt_of))

    # w[i][j]：来源窑 i 与目标窑 j（序号均从 1 开始）的共同器物数
    w = [[0] * (k + 1) for _ in range(m + 1)]
    for item_id in common:
        w[src_of[item_id]][tgt_of[item_id]] += 1

    pair_count = min(m, k)
    size = 1 << k
    full = size - 1

    # ---- 位掩码 DP：最大留窑数（恰好配满 pair_count 对才可行） ----
    g = [[-1] * size for _ in range(m + 2)]
    row_end = g[m + 1]
    for tmask in range(size):
        if tmask.bit_count() == pair_count:
            row_end[tmask] = 0
    for i in range(m, 0, -1):
        row = g[i]
        nxt = g[i + 1]
        wi = w[i]
        for tmask in range(size):
            best = nxt[tmask]  # 来源窑 i 不配对
            avail = full ^ tmask
            while avail:
                bit = avail & -avail
                j = bit.bit_length()  # bit 位 0 起 -> 目标窑序号 j（1 起）
                prev = nxt[tmask | bit]
                if prev >= 0:
                    cand = wi[j] + prev
                    if cand > best:
                        best = cand
                avail ^= bit
            row[tmask] = best

    # ---- 贪心重建字典序最小的配对序列 ----
    # 序列按来源序号升序；若某个最优完成配对了来源窑 i，则下一对必为
    # (i, j)，字典序恒小于任何以更大来源序号开头的序列，故逐窑取满足
    # 最优值的最小目标序号即可。
    pairs: list[tuple[int, int]] = []
    tmask = 0
    for i in range(1, m + 1):
        row = g[i]
        nxt = g[i + 1]
        wi = w[i]
        chosen_bit = 0
        chosen_j = 0
        avail = full ^ tmask
        while avail:
            bit = avail & -avail  # 目标序号升序枚举，首个可行者即最小
            j = bit.bit_length()
            prev = nxt[tmask | bit]
            if prev >= 0 and wi[j] + prev == row[tmask]:
                chosen_bit = bit
                chosen_j = j
                break
            avail ^= bit
        if chosen_bit:
            pairs.append((i, chosen_j))
            tmask |= chosen_bit
        # 否则来源窑 i 不配对（此时必有 g[i+1][tmask] == g[i][tmask]）

    # ---- 汇总留窑 / 换窑 / 单方独有编号（均按编号升序） ----
    pair_map = dict(pairs)
    stayed: list[str] = []
    moved: list[str] = []
    for item_id in common:
        if pair_map.get(src_of[item_id]) == tgt_of[item_id]:
            stayed.append(item_id)
        else:
            moved.append(item_id)

    only_in_source = sorted(set(src_of) - set(tgt_of))
    only_in_target = sorted(set(tgt_of) - set(src_of))

    return PlanComparison(
        pairs=tuple(pairs),
        stayed_item_ids=tuple(stayed),
        moved_item_ids=tuple(moved),
        only_in_source_item_ids=tuple(only_in_source),
        only_in_target_item_ids=tuple(only_in_target),
    )


def append_items(
    original_kilns: Sequence[Sequence[Ware]],
    new_items: list[Ware],
    capacity: int,
) -> list[Kiln]:
    """在已保存方案上追加新器物，求新增窑次最少的装窑方案。

    原窑及其次序保持不变；每件新器物可填入「剩余容量与温度区间交集均满足」
    的原窑（同一原窑可填入多件，按合并后的整体判定），或进入追加窑次
    （追加窑次与新建方案同规则：容量限制、温区交集、烧成温度取交集下界）。
    全局选择新增窑次数最少的方案；并列时最终窑序为「原窑次序 + 追加窑
    次序」，各窑编号升序，取整体字典序最小者。

    记新器物 k 件、原窑 m 个；因合并后器物总数不超过 14 且每个原窑至少
    一件原器物，必有 m + k ≤ 14。算法：

      1. 枚举新器物的全部 2^k 个子集，递推总体积与温度交集；由此得到
         「可单独成追加窑」标记与最少追加窑次 dp（同 solve 的子集表）。
      2. 对每个原窑求可接纳的新器物子集（剩余容量 + 温区交集）。
      3. 后缀 DP：best_from[j][mask] 表示用原窑 j..m-1 与追加窑安置
         新器物集合 mask 所需的最少追加窑次；best_from[m] 即第 1 步的
         dp，逐窑做子集 min 卷积回推，best_from[0][全集] 即全局最少
         新增窑次。
      4. 按原窑次序贪心重建：为每个原窑在「剩余器物仍可在最少追加窑次内
         安置」的可接纳子集中，选合并后编号升序列表字典序最小者；剩余
         新器物调用 solve 生成追加窑次（最少窑次 + 整体字典序最小）。

    :param original_kilns: 原方案各窑的器物（按原窑次序）
    :param new_items: 新器物（编号不得与原方案重复，至少 1 件）
    :param capacity: 单窑容量（沿用原方案）
    :raises CapacityExceededError: 新器物单件体积超过容量。
    :raises ValueError: 新器物为空，或合并后器物总数超过 14 件。
    """

    k = len(new_items)
    if k == 0:
        raise ValueError("至少需要一件新器物")
    n_orig = sum(len(kiln) for kiln in original_kilns)
    if n_orig + k > 14:
        raise ValueError(
            f"合并后器物数量不得超过 14 件（原 {n_orig} 件 + 新 {k} 件）"
        )

    # 新器物按编号字典序升序排列；位 i 对应排序后的第 i 件。
    ordered = sorted(new_items, key=lambda it: it.item_id)
    ids = [it.item_id for it in ordered]

    oversized = [it.item_id for it in ordered if it.volume > capacity]
    if oversized:
        raise CapacityExceededError(
            f"以下器物单件体积超过单窑容量 {capacity}，无法烧成: "
            + ", ".join(oversized)
        )

    size = 1 << k
    full = size - 1

    # ---- 1. 新器物子集的总体积、温度交集，与追加窑最少窑次 dp ----
    mask_volume = [0] * size
    mask_temp_low = [0] * size   # 交集下界（烧成温度）
    mask_temp_high = [0] * size  # 交集上界
    for mask in range(1, size):
        bit = mask & -mask
        i = bit.bit_length() - 1
        prev = mask ^ bit
        mask_volume[mask] = mask_volume[prev] + ordered[i].volume
        if prev == 0:
            mask_temp_low[mask] = ordered[i].temp_low
            mask_temp_high[mask] = ordered[i].temp_high
        else:
            mask_temp_low[mask] = max(mask_temp_low[prev], ordered[i].temp_low)
            mask_temp_high[mask] = min(mask_temp_high[prev], ordered[i].temp_high)

    valid = bytearray(size)  # 可单独成追加窑的子集
    for mask in range(1, size):
        if (
            mask_volume[mask] <= capacity
            and mask_temp_low[mask] <= mask_temp_high[mask]
        ):
            valid[mask] = 1

    dp = [k + 1] * size  # dp[mask]：mask 全部进追加窑的最少窑次
    dp[0] = 0
    for mask in range(1, size):
        best = dp[mask]
        sub = mask
        while sub:
            if valid[sub]:
                candidate = dp[mask ^ sub] + 1
                if candidate < best:
                    best = candidate
            sub = (sub - 1) & mask
        dp[mask] = best

    # ---- 2. 每个原窑可接纳的新器物子集 ----
    m = len(original_kilns)
    kiln_sorted_ids: list[list[str]] = []  # 原窑原器物编号（升序）
    kiln_volume: list[int] = []            # 原窑已用体积
    kiln_temp_low: list[int] = []          # 原窑温区交集下界
    feasible: list[bytearray] = []         # feasible[j][s]：子集 s 可填入原窑 j
    for kiln in original_kilns:
        used = sum(w.volume for w in kiln)
        low = max(w.temp_low for w in kiln)
        high = min(w.temp_high for w in kiln)
        kiln_sorted_ids.append(sorted(w.item_id for w in kiln))
        kiln_volume.append(used)
        kiln_temp_low.append(low)
        fj = bytearray(size)
        fj[0] = 1  # 空子集恒可填入（原窑保持不变）
        for mask in range(1, size):
            if (
                mask_volume[mask] <= capacity - used
                and max(low, mask_temp_low[mask])
                <= min(high, mask_temp_high[mask])
            ):
                fj[mask] = 1
        feasible.append(fj)

    # ---- 3. 后缀 DP：最少追加窑次 ----
    # best_from[j][mask]：用原窑 j..m-1 与追加窑安置 mask 的最少追加窑次
    best_from: list[list[int]] = [[]] * (m + 1)
    best_from[m] = dp
    for j in range(m - 1, -1, -1):
        nxt = best_from[j + 1]
        fj = feasible[j]
        cur = [0] * size
        for mask in range(size):
            best = nxt[mask]  # 原窑 j 不填入任何新器物
            sub = mask
            while sub:
                if fj[sub]:
                    candidate = nxt[mask ^ sub]
                    if candidate < best:
                        best = candidate
                sub = (sub - 1) & mask
            cur[mask] = best
        best_from[j] = cur

    min_appended = best_from[0][full]

    # ---- 4. 按原窑次序贪心重建整体字典序最小方案 ----
    # 不变式：进入原窑 j 时 best_from[j][rem] == min_appended，
    # 故空子集恒为可行候选，循环必能选出子集。
    rem = full
    kilns: list[Kiln] = []
    for j in range(m):
        fj = feasible[j]
        nxt = best_from[j + 1]
        orig = kiln_sorted_ids[j]
        best_sub = 0
        best_tuple: tuple[str, ...] | None = None
        sub = rem
        while True:
            if fj[sub] and nxt[rem ^ sub] <= min_appended:
                merged = tuple(
                    sorted(orig + [ids[i] for i in range(k) if (sub >> i) & 1])
                )
                if best_tuple is None or merged < best_tuple:
                    best_tuple = merged
                    best_sub = sub
            if sub == 0:
                break
            sub = (sub - 1) & rem

        assert best_tuple is not None
        firing_temp = kiln_temp_low[j]
        if best_sub:
            firing_temp = max(firing_temp, mask_temp_low[best_sub])
        kilns.append(
            Kiln(
                item_ids=best_tuple,
                total_volume=kiln_volume[j] + mask_volume[best_sub],
                firing_temp=firing_temp,
            )
        )
        rem ^= best_sub

    # ---- 剩余新器物进入追加窑次：复用 solve（最少窑次 + 字典序最小） ----
    if rem:
        kilns.extend(
            solve([ordered[i] for i in range(k) if (rem >> i) & 1], capacity)
        )

    return kilns


def cancel_items(
    original_kilns: Sequence[Sequence[Ware]],
    cancel_ids: Sequence[str],
    capacity: int,
) -> CancelResult:
    """在已保存方案上取消器物，对剩余器物做紧凑重排。

    取消器物只会从各窑中拿走成员（剩余器物温区不变、占用容量只减不增），
    故取消后仍非空的旧窑仍是可行窑：重排后的最少窑次 k 不超过非空旧窑数 m，
    每个新窑都能与一个（不同的）非空旧窑配对。被清空的旧窑无共同器物，
    不参与配对。择优优先级：

      1. 窑次数最少（对剩余器物复用子集表与最少窑次 dp）；
      2. 窑次相同时，非空旧窑与新窑一对一最优配对（配对数 = k）后
         仍留在对应窑的器物数最多；
      3. 仍并列时，取窑内编号升序、窑次按编号序列排序后的整体
         字典序最小方案。

    算法按非空旧窑序做位掩码 DP：状态 (已处理旧窑, 覆盖器物掩码, 新窑数 q)
    对每个留窑增益分别记录「已建窑的规范排序键」——不同增益互不可支配
    （当前少 1 分可能靠后续旧窑补回），同增益下向已升序列插入新窑的
    最终键随被插入键保序（插入位置只与键有关），故每档增益只保留最小
    已建键即可；每个旧窑可跳过，或与一个由未覆盖器物构成的可行新窑配对
    （留窑增益 = 该子集中原属此旧窑的器物数）。最终取最少窑次层中
    增益最大者的键；新旧窑配对并列时再调用 compare_kiln_assignments
    取配对序列字典序最小者。

    :param original_kilns: 原方案各窑的器物（按原窑次序，序号从 1 开始）
    :param cancel_ids: 取消的器物编号（非空、不重复，均须属于原方案，
        且不得取消全部器物；校验由调用方完成，此处对非法参数抛 ValueError）
    :param capacity: 单窑容量（沿用原方案）
    """

    canceled = frozenset(cancel_ids)
    if not canceled:
        raise ValueError("至少需要取消一件器物")
    if len(canceled) != len(list(cancel_ids)):
        raise ValueError("取消器物编号不得重复")

    old_wares = [w for kiln in original_kilns for w in kiln]
    by_id = {w.item_id: w for w in old_wares}
    unknown = sorted(canceled - set(by_id))
    if unknown:
        raise ValueError(f"取消编号不属于原方案: {', '.join(unknown)}")

    remaining = [w for w in old_wares if w.item_id not in canceled]
    if not remaining:
        raise ValueError("不得取消全部器物，取消后至少保留一件")

    # 旧窑在取消后的剩余器物（按旧窑次序；旧窑内编号升序）
    old_groups: list[tuple[str, ...]] = []
    for kiln in original_kilns:
        members = tuple(
            sorted(w.item_id for w in kiln if w.item_id not in canceled)
        )
        if members:
            old_groups.append(members)

    # ---- 剩余器物的子集表：可成窑标记、体积、烧成温度与最少窑次 ----
    tables = _build_tables(remaining, capacity)
    n = tables.n
    ids = tables.ids
    valid = tables.valid
    dp = tables.dp
    mask_volume = tables.mask_volume
    mask_temp_low = tables.mask_temp_low
    size = 1 << n
    full = size - 1
    min_kilns = dp[full]

    index_of = {item_id: i for i, item_id in enumerate(ids)}
    old_masks = [
        sum(1 << index_of[item_id] for item_id in members)
        for members in old_groups
    ]
    m = len(old_groups)

    def _members(group_mask: int) -> tuple[str, ...]:
        return tuple(ids[i] for i in range(n) if (group_mask >> i) & 1)

    # 各可行窑子集的成员元组（升序），仅算一次
    members_of: dict[int, tuple[str, ...]] = {
        mask: _members(mask) for mask in range(1, size) if valid[mask]
    }

    # DP（按「非空旧窑序」推进）：
    #   layers[q][mask] = {gain: 该增益下已建窑的最小规范排序键}
    # 含义：处理完若干个旧窑后，恰建 q 个新窑、覆盖器物集合 mask 时，
    # 各留窑增益对应的最小已建键。
    #
    # 注意不能在同一 (q, mask) 上只保留增益最大者：某条路径当前少得 1 分，
    # 可能是把高重合旧窑留待后续与更匹配的新窑配对而反超；不同增益互不可
    # 支配，必须分别保留。同增益下由「向升序列插入保序」的引理，最小的已建
    # 键一定给出最小的最终键，故每档增益只留一个键。
    # 每个旧窑可跳过，或与一个由未覆盖器物构成的可行新窑配对
    # （留窑增益 = 新窑中原属此旧窑的器物数）。
    Key = tuple[tuple[str, ...], ...]
    layers: list[list[dict[int, Key]]] = [[{} for _ in range(size)]]
    layers[0][0] = {0: ()}

    for oi in range(m):
        own = old_masks[oi]
        new_layers: list[list[dict[int, Key]]] = [
            [dict(cell) for cell in qm] for qm in layers
        ]
        while len(new_layers) <= min_kilns:
            new_layers.append([{} for _ in range(size)])
        # 处理完前 oi+1 个旧窑至多建 oi+1 个新窑，且只需到最少窑次 k
        for q in range(min(oi, min_kilns - 1) + 1):
            cells = layers[q]
            slots = min_kilns - q  # 含本窑配对后还需占用的窑次数上限
            for mask, cell in enumerate(cells):
                if not cell:
                    continue
                free = full ^ mask
                # 先枚举可行新窑，再对各增益档展开（增益档数量通常很少）
                groups: list[tuple[int, tuple[str, ...]]] = []
                sub = free
                while sub:
                    # 新窑 sub 可行，且其覆盖后剩余器物仍能在 slots-1 窑内装完
                    if valid[sub] and dp[free ^ sub] <= slots - 1:
                        groups.append((sub, members_of[sub]))
                    sub = (sub - 1) & free
                if not groups:
                    continue
                target = new_layers[q + 1]
                for gain_base, key_base in cell.items():
                    for sub, members in groups:
                        gain = gain_base + (sub & own).bit_count()
                        inserted = list(key_base)
                        insort(inserted, members)
                        new_key: Key = tuple(inserted)
                        tcell = target[mask | sub]
                        old_key = tcell.get(gain)
                        if old_key is None or new_key < old_key:
                            tcell[gain] = new_key
        # q 不会超过已处理旧窑数，也无需保留超过最少窑次的层
        del new_layers[min_kilns + 1:]
        layers = new_layers

    # 取最少窑次层中的最大留窑增益，及其最小整体字典序键
    final_cell = layers[min_kilns][full]
    assert final_cell
    best_gain = max(final_cell)
    plan_key = final_cell[best_gain]

    kilns = tuple(
        Kiln(
            item_ids=members,
            total_volume=mask_volume[
                sum(1 << index_of[item_id] for item_id in members)
            ],
            firing_temp=mask_temp_low[
                sum(1 << index_of[item_id] for item_id in members)
            ],
        )
        for members in plan_key
    )

    # 最优配对并列规则沿用方案对比：以取消后仍非空的旧窑为来源、
    # 新窑为目标做最大权匹配（被清空的旧窑无共同器物，不参与配对），
    # 配对序列字典序最小。
    comparison = compare_kiln_assignments(
        list(old_groups),
        list(plan_key),
    )

    # 非空旧窑的原窑次序号（old_groups 依原窑次序过滤得到）
    nonempty_old_index = [
        idx
        for idx, kiln in enumerate(original_kilns, start=1)
        if any(w.item_id not in canceled for w in kiln)
    ]
    pair_map = {
        nonempty_old_index[s - 1]: t for s, t in comparison.pairs
    }
    old_of: dict[str, int] = {}
    for idx, kiln in enumerate(original_kilns, start=1):
        for w in kiln:
            if w.item_id not in canceled:
                old_of[w.item_id] = idx
    new_of: dict[str, int] = {}
    for idx, members in enumerate(plan_key, start=1):
        for item_id in members:
            new_of[item_id] = idx
    stayed = tuple(
        sorted(
            item_id
            for item_id in new_of
            if pair_map.get(old_of[item_id]) == new_of[item_id]
        )
    )
    moved = tuple(
        sorted(
            item_id
            for item_id in new_of
            if pair_map.get(old_of[item_id]) != new_of[item_id]
        )
    )
    pairs_out = tuple(
        (nonempty_old_index[s - 1], t) for s, t in comparison.pairs
    )
    assert best_gain == len(stayed)

    return CancelResult(
        canceled_item_ids=tuple(sorted(canceled)),
        stayed_item_ids=stayed,
        moved_item_ids=moved,
        pairs=pairs_out,
        kilns=kilns,
    )
