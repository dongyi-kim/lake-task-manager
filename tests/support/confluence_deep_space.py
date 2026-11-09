"""A shared Space: ten roots, three heavy projects, favorites at depth four, depth eight."""


def install_deep_space(client):
    state = client.provider._client.app.state.confluence
    state.seed()
    state.pages.clear(); state.versions.clear()
    next_id = 900000
    created, favorites, targets = [], [], []

    def add(title, parent='', space='DL'):
        nonlocal next_id
        next_id += 1
        row = state.create({'id':str(next_id),'title':title,'space':{'key':space,'name':space},
            'ancestors':[{'id':parent}] if parent else [],
            'body':{'storage':{'value':'<h2>업무 문서</h2><p>탐색 검증용 본문</p>','representation':'storage'}}})
        created.append(row)
        return row['id']

    roots = [add(f'프로젝트 {i+1:02}') for i in range(10)]
    for project in range(3):
        for year in range(2):
            annual = add(f'{project+1} 프로젝트 {2025+year}년', roots[project])
            for module in range(4):
                module_id = add(f'{project+1}-{year} 모듈 {module+1}',annual)
                for section in range(3):
                    label = f'{project+1}-{year}-{module}-{section}'
                    folder = add(f'담당업무 {label}',module_id)
                    chosen = year == 1 and module == 3 and section == 2
                    if chosen:
                        favorites.append({'id':folder,'title':f'담당업무 {label}','space':'DL'})
                    for topic in range(4):
                        level5 = add(f'설계 {label}-{topic}',folder)
                        for sub in range(2):
                            level6 = add(f'상세 {label}-{topic}-{sub}',level5)
                            for leaf in range(2):
                                level7 = add(f'검토 {label}-{topic}-{sub}-{leaf}',level6)
                                title = f'Final Guide {label}-{topic}-{sub}-{leaf}'
                                level8 = add(title,level7)
                                if chosen and topic == 3 and sub == 1 and leaf == 1:
                                    targets.append({'id':level8,'title':title})
    for root in roots[3:]:
        for index in range(2):
            add(f'공통 운영 {root}-{index}',root)
    total = len(created)
    add('운영 Space','',space='OPS')
    children = {}
    for row in created:
        children.setdefault(row['parent'],[]).append(row)
    original = state.content

    def content(row, base='', expand=''):
        data = original(row,base,expand)
        if 'children.page' in expand:
            nested = children.get(row['id'],[])
            data['children'] = {'page':{'size':min(25,len(nested)),
                '_links':{'next':'?start=25'} if len(nested)>25 else {}}}
        return data

    state.content = content
    return {'count':total,'roots':roots,'favorites':favorites,'targets':targets}
