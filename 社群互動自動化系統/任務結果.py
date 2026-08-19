from dataclasses import dataclass,field
@dataclass
class TaskResult:status:str;found:int=0;reported:int=0;skipped:int=0;failed:int=0;issues:list[str]=field(default_factory=list)
