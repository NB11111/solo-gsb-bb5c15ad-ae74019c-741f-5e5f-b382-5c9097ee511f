"""请求 / 响应数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class ItemIn(BaseModel):
    """单件器物。"""

    item_id: str = Field(..., min_length=1, description="器物唯一编号（非空字符串）")
    volume: int = Field(..., strict=True, ge=1, description="体积（正整数）")
    temp_low: int = Field(..., strict=True, description="允许烧成温度区间下界")
    temp_high: int = Field(..., strict=True, description="允许烧成温度区间上界")

    @field_validator("item_id")
    @classmethod
    def _strip_and_check(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("器物编号不得为空白字符串")
        return v

    @model_validator(mode="after")
    def _check_interval(self):
        if self.temp_low > self.temp_high:
            raise ValueError(
                f"器物 {self.item_id} 的温度区间非法: "
                f"下界 {self.temp_low} 大于上界 {self.temp_high}"
            )
        return self


def _find_duplicate_ids(items: list[ItemIn]) -> list[str]:
    """返回器物列表中重复出现的编号（去重，按首次重复出现的顺序）。"""

    seen: set[str] = set()
    dup: list[str] = []
    for it in items:
        if it.item_id in seen and it.item_id not in dup:
            dup.append(it.item_id)
        seen.add(it.item_id)
    return dup


class PlanCreate(BaseModel):
    """排程请求。"""

    capacity: int = Field(..., strict=True, ge=1, description="单窑容量（正整数）")
    items: list[ItemIn] = Field(..., min_length=1, max_length=14)

    @model_validator(mode="after")
    def _unique_ids(self):
        dup = _find_duplicate_ids(self.items)
        if dup:
            raise ValueError(f"器物编号必须唯一，重复编号: {', '.join(dup)}")
        return self


class AppendItemsIn(BaseModel):
    """追加器物请求。"""

    items: list[ItemIn] = Field(
        ...,
        min_length=1,
        max_length=14,
        description="新器物（至少 1 件，编号不得与原方案重复）",
    )

    @model_validator(mode="after")
    def _unique_ids(self):
        dup = _find_duplicate_ids(self.items)
        if dup:
            raise ValueError(f"器物编号必须唯一，重复编号: {', '.join(dup)}")
        return self


class ItemOut(ItemIn):
    pass


class KilnOut(BaseModel):
    """一窑的排程结果。"""

    index: int = Field(..., description="窑次序号，从 1 开始")
    item_ids: list[str] = Field(..., description="本窑器物编号，按编号升序")
    total_volume: int
    firing_temp: int = Field(..., description="烧成温度（温度区间交集下界）")


class PlanOut(BaseModel):
    """已保存的排程方案。"""

    id: str
    capacity: int
    item_count: int
    kiln_count: int
    items: list[ItemOut]
    kilns: list[KilnOut]
    created_at: str


class ErrorOut(BaseModel):
    error: str
    message: str


class CapacityTargetIn(BaseModel):
    """目标窑次请求。"""

    target_kiln_count: int = Field(
        ..., strict=True, ge=1, description="目标窑次上限（1～原方案器物数）"
    )


class CapacityTargetOut(BaseModel):
    """最低容量求解结果（不改动原方案）。"""

    plan_id: str = Field(..., description="原方案 ID")
    target_kiln_count: int = Field(..., description="目标窑次上限")
    min_capacity: int = Field(..., description="使最少窑次不超过目标的最小单窑容量")
    kiln_count: int = Field(..., description="该容量下的实际窑次")
    kilns: list[KilnOut] = Field(..., description="该容量下的装窑明细")


class ProductionLimitIn(BaseModel):
    """限窑次接单请求。"""

    max_kilns: int = Field(
        ..., strict=True, ge=1, description="最多可用窑次（1～原方案器物数）"
    )


class ProductionLimitOut(BaseModel):
    """限窑次接单结果（只读计算，不改动原方案）。"""

    plan_id: str = Field(..., description="原方案 ID")
    max_kilns: int = Field(..., description="请求的窑次上限")
    kept_count: int = Field(..., description="保留（按期可烧）器物数")
    delayed_count: int = Field(..., description="延期器物数")
    kept_volume: int = Field(..., description="保留器物总体积")
    kept_item_ids: list[str] = Field(..., description="保留器物编号，按编号升序")
    delayed_item_ids: list[str] = Field(..., description="延期器物编号，按编号升序")
    kiln_count: int = Field(..., description="保留器物的实际窑次（最少窑次）")
    kilns: list[KilnOut] = Field(..., description="保留器物的实际装窑明细")


class PlanCompareIn(BaseModel):
    """方案对比请求。"""

    source_plan_id: str = Field(..., min_length=1, description="来源方案 ID")
    target_plan_id: str = Field(..., min_length=1, description="目标方案 ID")


class KilnPairOut(BaseModel):
    """一对配对的窑次（序号均从 1 开始）。"""

    source_index: int = Field(..., description="来源方案窑次序号")
    target_index: int = Field(..., description="目标方案窑次序号")


class PlanCompareOut(BaseModel):
    """两份方案的比较结果（只读计算，不改写任一方案）。"""

    source_plan_id: str = Field(..., description="来源方案 ID")
    target_plan_id: str = Field(..., description="目标方案 ID")
    common_count: int = Field(..., description="共同器物数")
    pair_count: int = Field(..., description="窑次配对数（两边窑次数的较小值）")
    kiln_pairs: list[KilnPairOut] = Field(
        ..., description="窑次配对，按来源窑次序号升序"
    )
    stayed_item_ids: list[str] = Field(
        ..., description="留在对应窑的共同器物编号，按编号升序"
    )
    moved_item_ids: list[str] = Field(
        ..., description="换窑的共同器物编号（含未落在配对窑内者），按编号升序"
    )
    only_in_source_item_ids: list[str] = Field(
        ..., description="仅出现在来源方案的器物编号，按编号升序"
    )
    only_in_target_item_ids: list[str] = Field(
        ..., description="仅出现在目标方案的器物编号，按编号升序"
    )
