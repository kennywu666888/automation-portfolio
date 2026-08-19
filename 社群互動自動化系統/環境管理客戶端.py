from dataclasses import dataclass
import time,requests
@dataclass(frozen=True)
class ProfileInfo:
    profile_id:str
    name:str
    group_name:str=''
    remark:str=''
    serial_number:str=''
    proxy_ip:str=''

def _extract_proxy_ip(data):
    keys=('ip','proxy_ip','proxy_host','host','proxy_server','server')
    containers=[data,data.get('proxy_config') or {},data.get('proxy') or {},data.get('proxy_info') or {},data.get('user_proxy_config') or {}]
    for container in containers:
        if not isinstance(container,dict):continue
        for key in keys:
            value=container.get(key)
            if value not in (None,''):return str(value).strip()
    return ''
@dataclass(frozen=True)
class BrowserInfo: profile_id:str; selenium_address:str; webdriver_path:str
class AdsPowerClient:
    def __init__(self,base_url,timeout=20,api_key=''):
        self.base=base_url.rstrip('/'); self.timeout=timeout
        key=str(api_key or '').strip()
        self.headers={'Authorization':f'Bearer {key}'} if key else {}
    def _get(self,path,params=None,retries=3):
        last=None
        for i in range(retries):
            try:
                r=requests.get(self.base+path,params=params or {},headers=self.headers,timeout=self.timeout);r.raise_for_status();d=r.json()
                if d.get('code')==0:return d
                last=RuntimeError(d.get('msg','AdsPower error'))
            except Exception as e:last=e
            time.sleep(1+i)
        raise RuntimeError(f'AdsPower API 失敗：{last}')
    def _post(self,path,payload=None,retries=3):
        last=None
        for i in range(retries):
            try:
                r=requests.post(self.base+path,json=payload or {},headers=self.headers,timeout=self.timeout);r.raise_for_status();d=r.json()
                if d.get('code')==0:return d
                last=RuntimeError(d.get('msg','AdsPower error'))
            except Exception as e:last=e
            time.sleep(1+i)
        raise RuntimeError(f'AdsPower API 失敗：{last}')
    def list_groups(self):
        d=self._get('/api/v1/group/list',{'page':1,'page_size':100})
        return [(str(x.get('group_id','')),str(x.get('group_name',''))) for x in d.get('data',{}).get('list',[])]
    def list_profiles(self,group_id='0'):
        out=[];page=1
        while True:
            p={'page':page,'page_size':100}
            if str(group_id) not in ('','0'):p['group_id']=group_id
            b=self._get('/api/v1/user/list',p).get('data',{}).get('list',[])
            out.extend(ProfileInfo(str(x.get('user_id','')),str(x.get('name','')),str(x.get('group_name','')),str(x.get('remark','')),str(x.get('serial_number','')),_extract_proxy_ip(x)) for x in b if x.get('user_id'))
            if len(b)<100:break
            page+=1;time.sleep(.8)
        return out
    def get_profile(self,pid):
        d=self._get('/api/v1/user/list',{'user_id':str(pid),'page':1,'page_size':10})
        rows=d.get('data',{}).get('list',[])
        for x in rows:
            if str(x.get('user_id',''))==str(pid):
                return ProfileInfo(str(x.get('user_id','')),str(x.get('name','')),str(x.get('group_name','')),str(x.get('remark','')),str(x.get('serial_number','')),_extract_proxy_ip(x))
        raise RuntimeError(f'AdsPower 找不到指定環境：{pid}')
    def open_browser(self,pid):
        d=self._get('/api/v1/browser/start',{'user_id':pid}).get('data',{})
        addr=str(d.get('ws',{}).get('selenium') or d.get('selenium') or '').replace('http://','').replace('https://','')
        path=str(d.get('webdriver',''))
        if not addr or not path:raise RuntimeError(f'AdsPower 未回傳 Selenium 資訊：{d}')
        return BrowserInfo(pid,addr,path)
    def close_browser(self,pid):self._get('/api/v1/browser/stop',{'user_id':pid})
    def rename_profile(self,pid,new_name):
        name=str(new_name or '').strip()
        if not pid or not name:raise RuntimeError('AdsPower 更名需要環境 ID 與新名稱')
        self._post('/api/v1/user/update',{'user_id':str(pid),'name':name})
        return True
    def delete_profile(self,pid,expected_name=''):
        profile_id=str(pid or '').strip()
        if not profile_id:raise RuntimeError('AdsPower 刪除需要環境 ID')
        expected=str(expected_name or '').strip()
        if expected:
            current=None
            for attempt in range(3):
                current=self.get_profile(profile_id)
                if current.name==expected:break
                time.sleep(.6*(attempt+1))
            if current is None or current.name!=expected:
                actual=current.name if current else '找不到'
                raise RuntimeError(
                    f'刪除前名稱核對失敗：預期={expected}，實際={actual}；已取消刪除'
                )
        self._post('/api/v1/user/delete',{'user_ids':[profile_id]})
        return True
