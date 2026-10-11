# Memory/allocator usage census over the comment/string-stripped mirror of src/.
# Usage: python3 -I mem_census.py <mirror_root>
# Prints: per-pattern sites/files, per-module breakdown for selected patterns.
import os, re, sys, collections

root = sys.argv[1]
P = collections.OrderedDict()
# --- allocator interface / objects
P['ObIAllocator_mention'] = r'\bObIAllocator\b'
P['ObIAllocator_ref_param'] = r'\bObIAllocator\s*&\s*\w+\s*[,)]'
P['ObIAllocator_ptr_param'] = r'\bObIAllocator\s*\*\s*\w+\s*(=\s*(NULL|nullptr)\s*)?[,)]'
P['ObIAllocator_ref_member'] = r'(?m)^\s+(common::)?ObIAllocator\s*&\s*\w+_\s*;'
P['ObIAllocator_ptr_member'] = r'(?m)^\s+(common::)?ObIAllocator\s*\*\s*\w+_\s*;'
P['ObArenaAllocator_member'] = r'(?m)^\s+(common::)?ObArenaAllocator\s+\w+_\s*;'
P['ObArenaAllocator_decl_any'] = r'\bObArenaAllocator\s+\w+\s*[;({]'
P['ObArenaAllocator_mention'] = r'\bObArenaAllocator\b'
P['ObSafeArenaAllocator_mention'] = r'\bObSafeArenaAllocator\b'
P['ObMalloc_decl'] = r'\b(common::)?ObMalloc\s+\w+\s*[;({]'
P['ObFIFOAllocator_mention'] = r'\bObFIFOAllocator\b'
P['ObConcurrentFIFOAllocator_mention'] = r'\bObConcurrentFIFOAllocator\b'
P['ObVSliceAlloc_mention'] = r'\bObVSliceAlloc\b'
P['ObSliceAlloc_mention'] = r'\bObSliceAlloc\b'
P['ObSmallAllocator_mention'] = r'\bObSmallAllocator\b'
P['ObWrapperAllocator_mention'] = r'\bObWrapperAllocator(WithAttr)?\b'
P['ModulePageAllocator_mention'] = r'\bModulePageAllocator\b'
P['ObMemAttr_mention'] = r'\bObMemAttr\b'
P['ObLabel_or_label_literal_attr'] = r'\bObMemAttr\s*\(\s*""'
P['ctx_id_explicit'] = r'\bObCtxIds::\w+'
P['ObMallocHookAttrGuard'] = r'\bObMallocHookAttrGuard\b'
# --- MemoryContext
P['MemoryContext_mention'] = r'\bMemoryContext\b'
P['MemoryContext_member'] = r'(?m)^\s+(lib::)?MemoryContext\s+\w+_\s*;'
P['CREATE_WITH_TEMP_CONTEXT'] = r'\bCREATE_WITH_TEMP_CONTEXT(_P)?\s*\('
P['WITH_CONTEXT'] = r'\bWITH_CONTEXT(_P)?\s*\('
P['CURRENT_CONTEXT'] = r'\bCURRENT_CONTEXT\b'
P['ROOT_CONTEXT'] = r'\bROOT_CONTEXT\b'
P['create_memory_context'] = r'\bcreate_memory_context\s*\(|\bCREATE_CONTEXT\s*\('
P['DESTROY_CONTEXT'] = r'\bDESTROY_CONTEXT\s*\(|\bdestroy_context\s*\('
P['get_arena_allocator'] = r'\bget_arena_allocator\s*\(\s*\)'
P['get_malloc_allocator'] = r'\bget_malloc_allocator\s*\(\s*\)'
# --- raw alloc / free / construct / destruct
P['raw_alloc_call'] = r'(\.|->)alloc(_aligned|_align)?\s*\('
P['ob_malloc_call'] = r'\bob_malloc(_align)?\s*\('
P['ob_free_call'] = r'\bob_free(_align)?\s*\('
P['member_free_call'] = r'(\.|->)free\s*\(\s*[\w*&(]'
P['placement_new'] = r'\bnew\s*\((?!\s*std::nothrow)'
P['placement_new_this'] = r'\bnew\s*\(\s*this\s*\)'
P['placement_new_addr'] = r'\bnew\s*\(\s*&'
P['OB_NEW_family'] = r'\b(OB_NEW|OB_NEWx|OB_NEW_ALIGN32|OB_NEW_ALIGN)\s*\('
P['OB_DELETE_family'] = r'\b(OB_DELETE|OB_DELETEx)\s*\('
P['MTL_NEW_DELETE'] = r'\b(MTL_NEW\w*|MTL_DELETE\w*)\s*\('
P['ob_delete_call'] = r'\bob_delete\s*\('
P['explicit_dtor'] = r'(->|\.)~\w+\s*\(\s*\)'
P['raw_new_expr'] = r'(?<![\w:])new\s+(?!\()[A-Za-z_][\w:<>]*'
P['raw_delete_expr'] = r'(?<![\w:])delete\s*(\[\s*\])?\s+[\w(*]'
# --- borrowed views and copies
P['ObString_member'] = r'(?m)^\s+(const\s+)?(common::)?ObString\s+\w+_\s*(\[[^\]]*\])?\s*;'
P['ob_write_string'] = r'\bob_write_string\s*\('
P['deep_copy_call'] = r'\bdeep_copy\w*\s*\('
P['raw_ptr_member'] = r'(?m)^\s+(const\s+)?[\w:]+(\s*<[^;{}()]*>)?\s*\*+\s*(const\s+)?\w+_\s*(\[[^\]]*\])?\s*;'
P['ptr_ref_slot'] = r'\b[A-Z]\w*\s*\*\s*&\s*\w+'
# --- containers
P['ObSEArray'] = r'\bObSEArray\s*<'
P['ObArray'] = r'\bObArray\s*<'
P['ObFixedArray'] = r'\bObFixedArray\s*<'
P['ObIArray'] = r'\bObIArray\s*<'
P['ObSqlArray_or_Ob2DArray'] = r'\b(Ob2DArray|ObSqlArray|ObSArray|ObSegmentArray)\s*<'
P['ObList'] = r'\bObList\s*<'
P['ObHashMap'] = r'\b(hash::)?ObHashMap\s*<'
P['ObHashSet'] = r'\b(hash::)?ObHashSet\s*<'
P['ObLinearHashMap'] = r'\bObLinearHashMap\s*<'
P['ObLinkHashMap'] = r'\bObLinkHashMap\s*<'
P['ObPooledAllocator'] = r'\bObPooledAllocator\s*<'
P['set_allocator_call'] = r'(\.|->)set_allocator\s*\('
P['prepare_allocate_or_init_arr'] = r'(\.|->)(prepare_allocate|reserve)\s*\('
# --- reset/reuse of allocators
P['alloc_reset_reuse'] = r'\b\w*(alloc|arena|allocator)\w*\s*(\.|->)\s*(reset|reuse|clear|reset_remain_one_page|fast_reuse)\s*\(\s*\)'
P['set_tracer_revert'] = r'(\.|->)(set_tracer|revert_tracer)\s*\('
# --- failure handling
P['OB_ALLOCATE_MEMORY_FAILED'] = r'\bOB_ALLOCATE_MEMORY_FAILED\b'
P['ret_eq_ALLOC_FAILED'] = r'\bret\s*=\s*(common::)?OB_ALLOCATE_MEMORY_FAILED\b'
P['branch_on_ALLOC_FAILED'] = r'(==|!=)\s*(common::)?OB_ALLOCATE_MEMORY_FAILED\b|\bOB_ALLOCATE_MEMORY_FAILED\s*(==|!=)'
P['isnull_assign_alloc'] = r'OB_ISNULL\s*\(\s*[\w.>\-\[\]]+\s*=\s*[^;]*?\b(alloc\w*|ob_malloc\w*|OB_NEW\w*|new_object)\s*\('
P['OB_EXCEED_QUERY_MEM_LIMIT'] = r'\bOB_EXCEED_QUERY_MEM_LIMIT\b'
P['OB_EXCEED_MEM_LIMIT'] = r'\bOB_EXCEED_MEM_LIMIT\b'
P['SMART_VAR_CALL'] = r'\bSMART_VAR\s*\(|\bSMART_VARS?_\d\s*\('
P['alloca_call'] = r'\b(alloca|ob_alloca|OB_ALLOCA)\s*\('

C = {k: re.compile(v) for k, v in P.items()}
sites = collections.Counter()
files = collections.Counter()
bymod = collections.defaultdict(collections.Counter)
modlines = collections.Counter()
for dp, dn, fn in os.walk(os.path.join(root, 'src')):
    for f in fn:
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, os.path.join(root, 'src'))
        mod = rel.split(os.sep)[0]
        s = open(p, encoding='utf-8', errors='replace').read()
        modlines[mod] += s.count('\n') + 1
        for k, r in C.items():
            n = len(r.findall(s))
            if n:
                sites[k] += n
                files[k] += 1
                bymod[k][mod] += n
print('%-34s %8s %7s' % ('pattern', 'sites', 'files'))
for k in P:
    print('%-34s %8d %7d' % (k, sites[k], files[k]))
MODS = ['sql', 'storage', 'oblib', 'share', 'observer', 'rootserver', 'pl', 'logservice', 'query', 'data_plane', 'standby']
print()
print('per-module sites (selected):')
sel = ['ObIAllocator_mention', 'ObIAllocator_ref_param', 'ObArenaAllocator_decl_any', 'MemoryContext_mention', 'placement_new',
       'explicit_dtor', 'OB_NEW_family', 'raw_alloc_call', 'ObString_member', 'ob_write_string', 'deep_copy_call',
       'ret_eq_ALLOC_FAILED', 'branch_on_ALLOC_FAILED', 'ObSEArray', 'ObArray', 'ObFixedArray', 'ObHashMap']
print('%-26s' % 'pattern' + ''.join('%11s' % m[:10] for m in MODS))
for k in sel:
    print('%-26s' % k + ''.join('%11d' % bymod[k][m] for m in MODS))
print('%-26s' % 'KLOC' + ''.join('%11d' % (modlines[m] // 1000) for m in MODS))
