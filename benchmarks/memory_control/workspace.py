"""Uniform, real file reads/searches over the tiny synthetic fixture only."""
from pathlib import Path, PurePosixPath


def relative(value, allow_empty=False):
    if not isinstance(value, str) or len(value)>240 or '\\' in value or ':' in value:
        raise ValueError('Use a relative POSIX fixture path')
    if value=='' and allow_empty:return ''
    path=PurePosixPath(value)
    if not value or path.is_absolute() or any(part in ('.','..') for part in value.split('/')):
        raise ValueError('Path must stay within the fixture')
    return str(path)


class Workspace:
    def __init__(self, root, *, max_bytes=40000):
        if type(max_bytes) is not int or not 1 <= max_bytes <= 100000:
            raise ValueError('Fixture byte bound must be between 1 and 100000')
        self.root=Path(root).resolve(strict=True)
        self.files={}
        for path in sorted(self.root.rglob('*')):
            if path.is_symlink():raise ValueError('Fixture links are not allowed')
            if path.is_file() and '__pycache__' not in path.parts:
                self.files[path.relative_to(self.root).as_posix()]=path.read_text(encoding='utf-8')
        if len(self.files)>30 or sum(len(text.encode()) for text in self.files.values())>max_bytes:
            raise ValueError('Fixture exceeds the compact experiment bound')

    def actions(self, actions):
        if not isinstance(actions,list) or not 1<=len(actions)<=6:
            raise ValueError('Request one to six file operations per turn')
        results=[]
        for action in actions:
            try:
                if not isinstance(action,dict) or set(action)!= {'op','value'}:
                    raise ValueError('Action needs only op and value')
                operation,value=action['op'],action['value']
                if operation=='list':
                    prefix=relative(value,allow_empty=True)
                    answer={'paths':[name for name in self.files if not prefix or name==prefix or name.startswith(prefix.rstrip('/')+'/')]}
                elif operation=='read':
                    name=relative(value)
                    if name not in self.files:raise ValueError('File not found in fixture')
                    answer={'path':name,'content':self.files[name]}
                elif operation=='search':
                    if not isinstance(value,str) or not value.strip() or len(value)>100:
                        raise ValueError('Search must be a literal string of 1 to 100 characters')
                    hits=[]
                    for name,content in self.files.items():
                        for number,line in enumerate(content.splitlines(),1):
                            if value.casefold() in line.casefold():
                                hits.append({'path':name,'line':number,'text':line[:240]})
                    answer={'matches':hits[:40],'total_matches':len(hits),'truncated':len(hits)>40}
                else:
                    raise ValueError('Available operations are list, search and read')
                results.append({'action':action,'ok':True,**answer})
            except (TypeError,ValueError) as exc:
                results.append({'action':action,'ok':False,'error':str(exc)})
        return results


def check_submission(files, allowed, read_paths):
    if not isinstance(files,list) or len(files)!=len(allowed):
        raise ValueError('Submit every owned module exactly once')
    outputs={}
    for row in files:
        if not isinstance(row,dict) or set(row)!={'path','content'}:
            raise ValueError('Each file needs only path and content')
        path=relative(row['path'])
        if path not in allowed or path in outputs:
            raise ValueError('File is unowned or duplicated')
        if path not in read_paths:
            raise ValueError('Read the current owned module before replacing it')
        content=row['content']
        if not isinstance(content,str) or len(content.encode())>24000:
            raise ValueError('Invalid or oversized source module')
        outputs[path]=content
    return outputs
