"""輸入欄位的共用型別：清單裡「每一項」的長度上限（只限制項目數時，單一項目仍可能塞進超長文字）。"""
from typing import Annotated

from pydantic import Field

Tag = Annotated[str, Field(max_length=60)]          # 職稱、產業、地點、分類這類短詞
Line = Annotated[str, Field(max_length=300)]        # 一行條列：工作內容、條件、追問
Link = Annotated[str, Field(max_length=500)]
