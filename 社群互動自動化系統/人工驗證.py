from __future__ import annotations

from dataclasses import dataclass


VERIFICATION_PREFIX = "驗証"


@dataclass(frozen=True)
class HumanVerificationResult:
    detected: bool
    reason: str = ""
    url: str = ""
    title: str = ""


def verification_profile_name(profile_name: str, profile_id: str = "") -> str:
    original = (profile_name or "").strip() or (profile_id or "").strip()
    if not original:
        raise RuntimeError("無法建立真人驗證環境名稱：名稱與環境 ID 都是空白")
    return (
        original
        if original.startswith(VERIFICATION_PREFIX)
        else f"{VERIFICATION_PREFIX}{original}"
    )


def detect_human_verification_page(driver) -> HumanVerificationResult:
    """Detect Facebook's explicit human-verification page.

    A generic verification word is not sufficient. The page must contain a
    supported human-verification phrase plus a Continue-style control, or use
    a checkpoint URL together with a human/person keyword.
    """
    try:
        payload = driver.execute_script(
            r"""
            const bodyText=((document.body && document.body.innerText) || '')
                .replace(/\s+/g,' ').trim().toLowerCase();
            const title=(document.title || '').replace(/\s+/g,' ').trim().toLowerCase();
            const url=(location.href || '').toLowerCase();
            const combined=`${title} ${bodyText}`;

            const exactPhrases=[
                "confirm you're human to use your account",
                "confirm you are human to use your account",
                "confirm that you're human to use your account",
                "confirm that you are human to use your account",
                "confirm you're human",
                "confirm you are human",
                "confirm that you're human",
                "confirm that you are human",
                "please confirm you're human",
                "please confirm you are human",
                "please confirm that you're human",
                "please confirm that you are human",
                "確認你是真人", "确认你是真人",
                "請確認你是真人", "请确认你是真人",
                "確認你是本人", "确认你是本人",
                "請確認你是本人", "请确认你是本人",
                "confirmez que vous êtes humain",
                "confirmer que vous êtes humain",
                "prouvez que vous êtes humain",
                "vérifiez que vous êtes humain",
                "kumpirmahing tao ka",
                "kumpirmahin na tao ka",
                "patunayang tao ka",
                "kumpirmahin mong tao ka"
            ];
            const exactPhraseFound=exactPhrases.some(p => combined.includes(p));
            const confirmWords=[
                'confirm','verify','verification',
                '確認','确认','驗證','验证','證明','证明',
                'confirmez','confirmer','vérifiez','verifiez','prouvez',
                'kumpirmahin','kumpirmahing','patunayan','patunayang'
            ];
            const humanWords=[
                'human','真人','本人','人類','人类','humain','tao'
            ];
            const hasConfirmWord=confirmWords.some(w => combined.includes(w));
            const hasHumanWord=humanWords.some(w => combined.includes(w));
            const keywordMatch=hasConfirmWord && hasHumanWord;
            const continueTerms=[
                'continue','繼續','继续','下一步','continuer','magpatuloy'
            ];
            const buttons=[...document.querySelectorAll(
                'button,[role="button"],input[type="submit"],input[type="button"],a[role="button"]'
            )];
            const hasContinue=buttons.some(b => {
                const text=((b.getAttribute('aria-label') || b.value ||
                    b.innerText || b.textContent || ''))
                    .replace(/\s+/g,' ').trim().toLowerCase();
                return continueTerms.some(term => text===term || text.includes(term));
            });
            const isCheckpoint=
                url.includes('/checkpoint/') ||
                url.includes('/checkpoint?') ||
                url.endsWith('/checkpoint');
            const detected=
                (exactPhraseFound && hasContinue) ||
                (keywordMatch && hasContinue) ||
                (isCheckpoint && hasHumanWord);
            let reason='';
            if(exactPhraseFound && hasContinue) reason='exact_phrase_and_continue';
            else if(keywordMatch && hasContinue) reason='keywords_and_continue';
            else if(isCheckpoint && hasHumanWord) reason='checkpoint_and_human_word';
            return {detected,reason,url,title};
            """
        ) or {}
    except Exception as exc:
        return HumanVerificationResult(
            False,
            reason=f"detection_error:{type(exc).__name__}:{exc}",
        )
    return HumanVerificationResult(
        bool(payload.get("detected")),
        reason=str(payload.get("reason") or ""),
        url=str(payload.get("url") or ""),
        title=str(payload.get("title") or ""),
    )
