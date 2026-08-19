import threading,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from 環境管理客戶端 import AdsPowerClient
from 瀏覽器工作階段 import BrowserSession
from 臉書健康檢查 import check_health
from notification_repository import NotificationRepository
from Telegram回報 import TelegramReporter, TelegramTargets
from notification_task import NotificationTask
from 診斷工具 import save_diagnostic
from 設定 import telegram_credentials
from 日誌 import profile_logger
from 任務結果 import TaskResult
from 社團留言任務 import GroupCommentTask, GroupUrlQueue
from 人工驗證 import (
    detect_human_verification_page,
    verification_profile_name,
)
from 臉書帳號狀態 import (
    detect_facebook_account_status,
    login_profile_name,
    sleep_mode_profile_name,
    suspended_profile_name,
    tunnel_profile_name,
)
from 個人資料工具 import sort_profiles_by_number
class MonitorEngine:
    def __init__(self,s,profiles,logger,progress=lambda p,r:None):self.s=s;self.profiles=profiles;self.log=logger;self.progress=progress;self.stop=threading.Event();self.repo=NotificationRepository();t,c1,c2=telegram_credentials();self.reporter=TelegramReporter(t,TelegramTargets(c1,c2),bool(s.get('telegram_enabled',True)));self.running=False;self.group_queue=None
    def request_stop(self):self.stop.set()
    def run(self):
        self.running=True;cycles=int(self.s.get('cycles',1));n=0
        try:
            while not self.stop.is_set() and (cycles==0 or n<cycles):
                n+=1
                delete_after_claim=bool(self.s.get(
                    'delete_group_url_after_success',
                    self.s.get('delete_group_url_after_claim',True),
                ))
                if self.s.get('enable_group_comment_task') and (
                    self.group_queue is None or not delete_after_claim
                ):
                    # When source deletion is disabled, reload group.txt every
                    # cycle.  The queue still prevents duplicate claims inside
                    # the same cycle, while later cycles can reuse the list.
                    self.group_queue=GroupUrlQueue(
                        delete_after_claim=delete_after_claim
                    )
                items=sort_profiles_by_number(self.profiles)
                order=' → '.join(p.name for p in items[:30])
                if len(items)>30:order+=f' → …（另有 {len(items)-30} 個）'
                self.log.info('第 %d 輪實際提交順序：%s',n,order)
                requested_workers=min(max(1,int(self.s.get('threads',1))),len(items) or 1)
                workers=requested_workers
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    fs={ex.submit(self._profile,p):p for p in items}
                    for f in as_completed(fs):
                        profile=fs[f]
                        try:r=f.result()
                        except Exception as exc:
                            self.log.exception('環境工作執行緒異常：%s',profile.name)
                            r=TaskResult('FAILED',failed=1,issues=[str(exc)])
                        self.log.info(
                            '環境執行完成：%s｜狀態=%s｜發現=%d｜成功/回報=%d｜跳過=%d｜失敗=%d',
                            profile.name,r.status,r.found,r.reported,r.skipped,r.failed,
                        )
                        self.progress(profile,r)
                if cycles==0 or n<cycles:
                    if self.stop.wait(float(self.s.get('cycle_wait_minutes',30))*60):break
        except Exception:
            self.log.exception('執行引擎異常停止')
            raise
        finally:
            self.running=False
            self.log.info('執行引擎已結束｜已完成輪數=%d｜使用者停止=%s',n,self.stop.is_set())
    def _profile(self,p):
        plog,lp=profile_logger(p.name)
        plog.info('開始執行環境：%s',p.name)
        client=AdsPowerClient(
            self.s['adspower_base_url'],
            api_key=self.s.get('adspower_api_key',''),
        )
        bs=None;task=None;stage='start';group_result=None;profile_deleted=False;profile_closed=False;force_close_without_delete=False
        personal_profile_url=''
        try:
            # Global preflight: this runs before group comments and notification
            # replies, so a human-verification profile cannot perform any task.
            stage='verification_open_browser'
            info=client.open_browser(p.profile_id)
            bs=BrowserSession(info)
            driver=bs.connect()
            bs.switch_to_facebook()
            stage='human_verification'
            if self.stop.wait(1.5):
                return TaskResult('STOPPED')
            verification=detect_human_verification_page(driver)
            if verification.reason.startswith('detection_error:'):
                plog.warning('真人驗證頁偵測失敗，繼續其他狀態判斷：%s',verification.reason)

            account_status=detect_facebook_account_status(driver)
            if account_status.reason.startswith('detection_error:'):
                plog.warning('Facebook 帳號狀態偵測失敗，繼續一般流程：%s',account_status.reason)

            # Preserve AdsPower tab 1. If the Facebook work tab is not already
            # the owner's profile, recover the unique Timeline URL from Home,
            # navigate to it, and verify it before any RC18 task starts.
            if not verification.detected and not account_status.detected:
                stage='cache_startup_personal_profile'
                personal_profile_url=bs.ensure_startup_personal_profile_url(
                    self.stop
                )
                plog.info(
                    '[%s] 已確認並進入本人個人主頁：%s',
                    p.name,personal_profile_url,
                )

            # Sleep Mode normally appears on the Notifications page. When the
            # notification task is enabled, open that page during preflight so
            # the profile is removed before any notification is processed.
            if (
                not verification.detected
                and not account_status.detected
                and self.s.get('enable_notification_task',True)
            ):
                stage='notification_status_preflight'
                driver.get('https://www.facebook.com/notifications')
                if self.stop.wait(2.5):
                    return TaskResult('STOPPED')
                account_status=detect_facebook_account_status(driver)
                if account_status.reason.startswith('detection_error:'):
                    plog.warning(
                        'Notifications 狀態偵測失敗，繼續一般流程：%s',
                        account_status.reason,
                    )

            status_kind=''
            status_label=''
            status_reason=''
            status_url=''
            if verification.detected:
                status_kind='verification'
                status_label='確認真人驗證'
                new_name=verification_profile_name(p.name,p.profile_id)
                status_reason=verification.reason
                status_url=verification.url
            elif account_status.kind=='suspended':
                status_kind='suspended'
                status_label='帳號已停權'
                new_name=suspended_profile_name(p.name,p.profile_id)
                status_reason=account_status.reason
                status_url=account_status.url
            elif account_status.kind=='sleep_mode':
                status_kind='sleep_mode'
                status_label='Sleep Mode'
                new_name=sleep_mode_profile_name(p.name,p.profile_id)
                status_reason=account_status.reason
                status_url=account_status.url
            elif account_status.kind=='tunnel_connection_failed':
                status_kind='tunnel_connection_failed'
                status_label='代理連線失敗'
                new_name=tunnel_profile_name(p.name,p.profile_id)
                status_reason=account_status.reason
                status_url=account_status.url
            elif account_status.kind=='login_page':
                status_kind='login_page'
                status_label='Facebook 登入頁'
                new_name=login_profile_name(p.name,p.profile_id)
                status_reason=account_status.reason
                status_url=account_status.url

            if status_kind:
                force_close_without_delete=(status_kind=='tunnel_connection_failed')
                if new_name!=p.name:
                    client.rename_profile(p.profile_id,new_name)
                plog.warning(
                    '[%s] 偵測到%s，環境名稱=%s｜判斷=%s｜網址=%s',
                    p.name,status_label,new_name,status_reason,status_url,
                )
                issue=f'偵測到{status_label}；環境已更名為「{new_name}」'
                if status_kind=='tunnel_connection_failed':
                    # Proxy/Tunnel expiry is recoverable.  Always close the
                    # browser but never send this profile to permanent delete,
                    # even when delete_verified_profile is enabled.
                    bs.detach();bs=None;driver=None
                    stage='tunnel_connection_failed_close_browser'
                    client.close_browser(p.profile_id)
                    profile_closed=True
                    issue+='；環境已更名為 IP到期、關閉並保留（未刪除）'
                    plog.warning(issue)
                elif bool(self.s.get('delete_verified_profile',False)):
                    # Release Selenium first, then close and delete only the
                    # exact profile ID whose renamed value can be read back.
                    bs.detach();bs=None;driver=None
                    stage=f'{status_kind}_close_browser'
                    client.close_browser(p.profile_id)
                    time.sleep(1.0)
                    stage=f'{status_kind}_delete_profile'
                    client.delete_profile(p.profile_id,expected_name=new_name)
                    profile_deleted=True
                    issue+=f'；AdsPower 環境已刪除（ID={p.profile_id}）'
                    plog.critical(issue)
                return TaskResult('FAILED',skipped=1,failed=1,issues=[issue])

            if self.s.get('enable_group_comment_task'):
                # The Playwright-based group task owns its connection, so
                # release the Selenium preflight reference before it starts.
                bs.detach();bs=None;driver=None
                stage='group_comment'
                group_result=GroupCommentTask(
                    p,self.s,plog,self.stop,self.group_queue
                ).run()
                if self.stop.is_set():
                    return group_result
                stage='open_browser'
                info=client.open_browser(p.profile_id)
                bs=BrowserSession(info)
                d=bs.connect()
                bs.switch_to_facebook()
                bs.set_personal_profile_url(personal_profile_url)
                if not self.s.get('enable_notification_task',True):
                    stage='personal_profile'
                    bs.go_personal_profile(personal_profile_url,self.stop)
                    return group_result
            else:
                # Notification-only runs reuse the verified Selenium session.
                d=driver
            stage='health'
            h=check_health(d)
            if h.status!='healthy':
                raise RuntimeError(f'Health Check：{h.status} {h.detail}')
            task=NotificationTask(d,p,self.s,self.repo,self.reporter,plog,self.stop)
            stage='task'
            r=task.run()
            stage='personal_profile'
            bs.go_personal_profile(personal_profile_url,self.stop)
            if group_result:
                statuses={group_result.status,r.status}
                status='FAILED' if 'FAILED' in statuses else ('PARTIAL' if 'PARTIAL' in statuses else ('STOPPED' if 'STOPPED' in statuses else 'SUCCESS'))
                return TaskResult(status,found=group_result.found+r.found,reported=group_result.reported+r.reported,skipped=group_result.skipped+r.skipped,failed=group_result.failed+r.failed,issues=group_result.issues+r.issues)
            return r
        except Exception as e:
            plog.exception('Profile 失敗')
            if bs and bs.driver:
                try:save_diagnostic(bs.driver,p.name,p.profile_id,stage,e,self.s,getattr(task,'all_candidates',getattr(task,'candidates',[])),lp)
                except Exception:pass
            return TaskResult('FAILED',failed=1,issues=[str(e)])
        finally:
            if bs:bs.detach()
            if (force_close_without_delete or self.s.get('close_browser')) and not profile_deleted and not profile_closed:
                try:client.close_browser(p.profile_id)
                except Exception:pass
