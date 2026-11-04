from pydantic import BaseModel, Field, ConfigDict
from dataclasses import dataclass
from typing import Annotated


@dataclass(frozen=True)
class Flag:
    llm_as_a_judge: bool = True


class DetailItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    detail: Annotated[str, Flag] = Field(
        alias="詳細内容",
        description="明細表の「内容」列。不具合・調整内容などの詳細内容",
    )
    department: str | None = Field(
        default=None, alias="関係部署", description="対応する関係部署"
    )
    confirmer: str | None = Field(
        default=None, alias="処置確認者", description="処置を確認した担当者"
    )


class MessageMemo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_name: str | None = Field(default=None, alias="工事名", description="工事名")
    model_type: str | None = Field(
        default=None,
        alias="型式",
        description="型式（「型式・調整期間」が1つのセルの場合は型式部分のみ）",
    )
    adjustment_period: str | None = Field(
        default=None,
        alias="調整期間",
        description="調整期間（記載どおりの表記。「型式・調整期間」が1つのセルの場合は調整期間部分のみ）",
    )
    work_number: str | None = Field(default=None, alias="工番", description="工番")
    approval_name: str | None = Field(
        default=None, alias="承認", description="承認欄の氏名"
    )
    approval_date: str | None = Field(
        default=None, alias="承認日", description="承認欄の日付:YYYY.MM.DD形式"
    )
    check_name: str | None = Field(
        default=None, alias="確認", description="確認欄の氏名"
    )
    check_date: str | None = Field(
        default=None, alias="確認日", description="確認欄の日付:YYYY.MM.DD形式"
    )
    author_name: str | None = Field(
        default=None, alias="作成者", description="作成欄の氏名"
    )
    author_date: str | None = Field(
        default=None, alias="作成日", description="作成欄の日付:YYYY.MM.DD形式"
    )
    notes: Annotated[str, Flag] | None = Field(
        default=None, alias="連絡事項", description="連絡事項（記載がなければ null）"
    )
    details: list[DetailItem] = Field(
        default_factory=list,
        alias="詳細",
        description="伝言メモの明細表の各行（全ページ分）。測定結果記録など他の表は含めない",
    )
