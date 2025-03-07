import os
import re
import json
import traceback
import chromadb
import hashlib
from pathlib import Path

import nltk
nltk.download('punkt')
from nltk.tokenize import word_tokenize

FILE_DIR = Path(os.path.dirname(os.path.realpath(__file__))).parent.parent.absolute()
DATABASE_PATH = os.path.join(FILE_DIR, './my_graphrag/vector_database')
DB_TMP_FILE_PATH = os.path.join(FILE_DIR, './my_graphrag/db_tmp_file.txt')
GROUP_ID_TMP_FILE_PATH = os.path.join(FILE_DIR, './my_graphrag/group_id_tmp_file.txt')
COLLECTION_GROUP = 'group'
COLLECTION_PAPER = 'paper'
COLLECTION_CHUNK = 'chunk'
COLLECTION_RELATIONSHIP = 'relationship'
COLLECTION_COMMUNITY_REPORT = 'community_report'
COLLECTION_SUMMARY = 'summary'


def save_new_item(collection_name: str, documents: str, metadatas: dict):
    client = chromadb.PersistentClient(path=get_db_path())
    collection = client.get_or_create_collection(name=collection_name)

    all_data = collection.get()

    last_ids = 0
    for ids in all_data['ids']:
        last_ids = max(last_ids, int(ids))

    new_ids = last_ids + 1
    new_ids = str(new_ids)
    collection.add(
        documents=[
            documents
        ],
        metadatas=[
            metadatas
        ],
        ids=[
            new_ids
        ]
    )

    return new_ids


def get_id(collection_name: str, query_content: str, metadatas=''):
    group_id = get_group_id_by_tmp_file()
    group_id_validity = check_group_id(group_id)

    ids = '0'
    try:
        client = chromadb.PersistentClient(path=get_db_path())
        collection = client.get_collection(name=collection_name)
        all_data = collection.get()

        if ids == '0':
            query_content_clean = re.sub(r'\s+', '', query_content)
            for i in range(len(all_data['ids'])):
                if group_id_validity and 'group_id' in all_data['metadatas'][i] and all_data['metadatas'][i]['group_id'] != group_id:
                    continue

                documents = all_data['documents'][i]
                if metadatas:
                    m_text = ''
                    try:
                        m_text = all_data['metadatas'][i][metadatas]
                    except:
                        pass
                    if m_text != '':
                        documents = m_text

                documents_clean = re.sub(r'\s+', '', documents)
                if query_content_clean in documents_clean or query_content in documents:
                    ids = all_data['ids'][i]
                    break

        if ids == '0':
            try:
                results = collection.query(
                    query_texts=[query_content],
                    n_results=1,
                    where=None if not group_id_validity else {'group_id': group_id}
                )
                ids = results['ids'][0][0]
            except:
                pass

        if ids == '0':
            split_text = query_content.split('\n')
            for text in split_text:
                text_clean = re.sub(r'\s+', '', text)
                for i in range(len(all_data['ids'])):
                    if group_id_validity and 'group_id' in all_data['metadatas'][i] and all_data['metadatas'][i]['group_id'] != group_id:
                        continue

                    documents = all_data['documents'][i]
                    if metadatas:
                        m_text = ''
                        try:
                            m_text = all_data['metadatas'][i][metadatas]
                        except:
                            pass
                        if m_text != '':
                            documents = m_text

                    documents_clean = re.sub(r'\s+', '', documents)
                    if text_clean in documents_clean or text in documents:
                        ids = all_data['ids'][i]
                        break
                if ids != '0':
                    break

    except:
        pass

    return ids


def save_new_group(group_name):
    # group
    # ids: group id
    # documents: group_name
    # metadatas: group_name

    group_id = save_new_item(
        COLLECTION_GROUP,
        group_name,
        {
            'group_name': group_name,
        }
    )

    return group_id


def save_new_paper(paper_content, paper_name, group_id):
    # paper
    # ids: paper id
    # documents: paper_name
    # metadatas: paper_name, group_id

    hash_value = hashlib.sha256(paper_content.encode()).hexdigest()

    paper_id = save_new_item(
        COLLECTION_PAPER,
        paper_content,
        {
            'paper_name': paper_name,
            'group_id': group_id,
            'hash': hash_value,
        }
    )

    return paper_id


def save_new_chunk(chunk, paper_id, group_id, denoising_chunk=''):
    # chunk
    # ids: chunk id
    # documents: chunk_content
    # metadatas: paper_id

    sub_chunks = split_text_into_sub_chunks(denoising_chunk) if denoising_chunk else []

    chunk_id = save_new_item(
        COLLECTION_CHUNK,
        chunk,
        {
            'paper_id': paper_id,
            'group_id': group_id,
            'denoising_chunk': denoising_chunk,
            'sub_chunks': json.dumps(sub_chunks),
        }
    )

    return chunk_id


def save_new_relationship(chunk, source_entity_name, target_entity_name, relationship_description, relationship_strength):
    # relationship
    # ids: relationship id
    # documents: relationship_description
    # metadatas: source entity name, target entity name, relationship description, relationship strength, chunk id

    chunk_id = get_id(COLLECTION_CHUNK, chunk, metadatas='denoising_chunk')

    relationship_id = save_new_item(
        COLLECTION_RELATIONSHIP,
        relationship_description,
        {
            'source_entity_name': source_entity_name,
            'target_entity_name': target_entity_name,
            'relationship_description': relationship_description,
            'relationship_strength': relationship_strength,
            'chunk_id': chunk_id,
        }
    )

    return relationship_id


def save_new_community_report(index_prompt3_input_text, community_report_text):
    # community report
    # ids: community report id
    # documents: community report text
    # metadatas: relationship ids, title, summary, rating, rating explanation, findings (<insight> <insight_summary> ... </insight_summary> <insight_explanation> ... </insight_explanation> </insight>)

    group_id = get_group_id_by_tmp_file()
    _, group_chunk_id_list, _, _, _ = get_ref_ids_for_group(group_id)

    descriptions = re.findall(r'</target><description>(.*?)</description>', index_prompt3_input_text, re.DOTALL)
    chunk_id_list = []

    if descriptions:
        client = chromadb.PersistentClient(path=get_db_path())
        collection = client.get_collection(name=COLLECTION_RELATIONSHIP)

        for des in descriptions:
            results = collection.query(
                query_texts=[des],
                n_results=1
            )

            chunk_id = results['metadatas'][0][0]['chunk_id']
            if chunk_id in group_chunk_id_list:
                chunk_id_list.append(chunk_id)

        chunk_id_list = list(set(chunk_id_list))

    if check_group_id(group_id) and chunk_id_list and community_report_text:
        report_id = save_new_item(
            COLLECTION_COMMUNITY_REPORT,
            community_report_text,
            {
                'chunk_id_list': json.dumps(chunk_id_list),
                'group_id': group_id,
            }
        )

        return report_id

    return None


def save_new_summary(summary_text, chunk_id_list, from_base_chunk, root_summary, group_id):
    # summary chunk
    # ids: summary chunk id
    # documents: summary text
    # metadatas: chunk_id_list
    summary_id = save_new_item(
        COLLECTION_SUMMARY,
        summary_text,
        {
            'chunk_id_list': json.dumps(chunk_id_list),
            'from_base_chunk': from_base_chunk,
            'root_summary': root_summary,
            'group_id': group_id,
        }
    )

    return summary_id


def get_all_community_reports():
    report_list = []
    try:
        client = chromadb.PersistentClient(path=get_db_path())
        collection = client.get_collection(name=COLLECTION_COMMUNITY_REPORT)

        all_data = collection.get()
        for i in range(len(all_data['ids'])):
            report_list.append(
                {
                    'report_id': all_data['ids'][i],
                    'report_content': all_data['documents'][i],
                    'chunk_id_list': json.loads(all_data['metadatas'][i]['chunk_id_list']),
                    'group_id': all_data['metadatas'][i]['group_id'],
                }
            )

        report_list.sort(key=lambda x: int(x['report_id']), reverse=False)
    except Exception as e:
        # print(e)
        # traceback.print_exc()
        pass

    return report_list


def get_all_chunks():
    chunk_list = []
    try:
        client = chromadb.PersistentClient(path=get_db_path())
        collection = client.get_collection(name=COLLECTION_CHUNK)

        all_data = collection.get()
        for i in range(len(all_data['ids'])):
            chunk_list.append(
                {
                    'chunk_id': all_data['ids'][i],
                    'chunk_content': all_data['documents'][i],
                    'paper_id': all_data['metadatas'][i]['paper_id'],
                    'group_id': all_data['metadatas'][i]['group_id'],
                    'denoising_chunk': all_data['metadatas'][i]['denoising_chunk'],
                    'sub_chunks': json.loads(all_data['metadatas'][i]['sub_chunks']),
                }
            )

        chunk_list.sort(key=lambda x: int(x['chunk_id']), reverse=False)
    except Exception as e:
        # print(e)
        # traceback.print_exc()
        pass

    return chunk_list


def get_all_summary_chunks():
    summary_list = []
    try:
        client = chromadb.PersistentClient(path=get_db_path())
        collection = client.get_collection(name=COLLECTION_SUMMARY)

        all_data = collection.get()
        for i in range(len(all_data['ids'])):
            summary_list.append(
                {
                    'summary_id': all_data['ids'][i],
                    'summary_content': all_data['documents'][i],
                    'chunk_id_list': json.loads(all_data['metadatas'][i]['chunk_id_list']),
                    'from_base_chunk': all_data['metadatas'][i]['from_base_chunk'],
                    'root_summary': all_data['metadatas'][i]['root_summary'],
                    'group_id': all_data['metadatas'][i]['group_id'],
                }
            )

        summary_list.sort(key=lambda x: int(x['summary_id']), reverse=False)
    except Exception as e:
        # print(e)
        # traceback.print_exc()
        pass

    return summary_list


def get_all_papers():
    paper_list = []
    try:
        client = chromadb.PersistentClient(path=get_db_path())
        collection = client.get_collection(name=COLLECTION_PAPER)

        all_data = collection.get()
        for i in range(len(all_data['ids'])):
            paper_list.append(
                {
                    'paper_id': all_data['ids'][i],
                    'paper_content': all_data['documents'][i],
                    'paper_name': all_data['metadatas'][i]['paper_name'],
                    'group_id': all_data['metadatas'][i]['group_id'],
                    'hash': all_data['metadatas'][i]['hash'],
                }
            )

        paper_list.sort(key=lambda x: int(x['paper_id']), reverse=False)
    except Exception as e:
        # print(e)
        # traceback.print_exc()
        pass

    return paper_list


def get_all_groups():
    group_list = []
    try:
        client = chromadb.PersistentClient(path=get_db_path())
        collection = client.get_collection(name=COLLECTION_GROUP)

        all_data = collection.get()
        for i in range(len(all_data['ids'])):
            group_list.append(
                {
                    'group_id': all_data['ids'][i],
                    'group_name': all_data['metadatas'][i]['group_name'],
                }
            )

        group_list.sort(key=lambda x: int(x['group_id']), reverse=False)

    except Exception as e:
        # print(e)
        # traceback.print_exc()
        pass

    return group_list


def get_all_relationships():
    relationship_list = []
    try:
        client = chromadb.PersistentClient(path=get_db_path())
        collection = client.get_collection(name=COLLECTION_RELATIONSHIP)

        all_data = collection.get()
        for i in range(len(all_data['ids'])):
            relationship_list.append(
                {
                    'relationship_id': all_data['ids'][i],
                    'source_entity_name': all_data['metadatas'][i]['source_entity_name'],
                    'target_entity_name': all_data['metadatas'][i]['target_entity_name'],
                    'relationship_description': all_data['metadatas'][i]['relationship_description'],
                    'relationship_strength': all_data['metadatas'][i]['relationship_strength'],
                    'chunk_id': all_data['metadatas'][i]['chunk_id'],
                }
            )

        relationship_list.sort(key=lambda x: int(x['relationship_id']), reverse=False)

    except Exception as e:
        # print(e)
        # traceback.print_exc()
        pass

    return relationship_list


def get_ref_id_of_chunk(chunk_id):
    paper_id = None
    group_id = None
    try:
        client = chromadb.PersistentClient(path=get_db_path())
        collection = client.get_collection(name=COLLECTION_CHUNK)

        results = collection.get(
            ids=[str(chunk_id)]
        )

        paper_id = results['metadatas'][0]['paper_id']
        group_id = results['metadatas'][0]['group_id']
    except Exception as e:
        # print(e)
        # traceback.print_exc()
        pass

    return paper_id, group_id


def count_all_collection():
    client = chromadb.PersistentClient(path=get_db_path())

    # group
    group_count = 0
    try:
        collection = client.get_collection(name=COLLECTION_GROUP)
        all_data = collection.get()
        group_count = len(all_data['ids'])
    except Exception as e:
        # print(e)
        # traceback.print_exc()
        pass
    print('count of group:', group_count)

    # paper
    paper_count = 0
    try:
        collection = client.get_collection(name=COLLECTION_PAPER)
        all_data = collection.get()
        paper_count = len(all_data['ids'])
    except Exception as e:
        # print(e)
        # traceback.print_exc()
        pass
    print('count of paper:', paper_count)

    # chunk
    chunk_count = 0
    try:
        collection = client.get_collection(name=COLLECTION_CHUNK)
        all_data = collection.get()
        chunk_count = len(all_data['ids'])
    except Exception as e:
        # print(e)
        # traceback.print_exc()
        pass
    print('count of chunk:', chunk_count)

    # relationship
    relationship_count = 0
    try:
        collection = client.get_collection(name=COLLECTION_RELATIONSHIP)
        all_data = collection.get()
        relationship_count = len(all_data['ids'])
    except Exception as e:
        # print(e)
        # traceback.print_exc()
        pass
    print('count of relationship:', relationship_count)

    # community report
    report_count = 0
    try:
        collection = client.get_collection(name=COLLECTION_COMMUNITY_REPORT)
        all_data = collection.get()
        report_count = len(all_data['ids'])
    except Exception as e:
        # print(e)
        # traceback.print_exc()
        pass
    print('count of community report:', report_count)

    # summary
    summary_count = 0
    try:
        collection = client.get_collection(name=COLLECTION_SUMMARY)
        all_data = collection.get()
        summary_count = len(all_data['ids'])
    except Exception as e:
        # print(e)
        # traceback.print_exc()
        pass
    print('count of summary:', summary_count)


def query_base_chunk(query_text, top_k=20, query_group_id=-1):
    client = chromadb.PersistentClient(path=get_db_path())
    collection = client.get_collection(name=COLLECTION_CHUNK)

    results = collection.query(
        query_texts=[query_text],
        n_results=top_k,
        where=None if query_group_id == -1 else {'group_id': str(query_group_id)}
    )

    result_list = []
    for i in range(len(results['ids'][0])):
        chunk_id = results['ids'][0][i]
        paper_id, group_id = get_ref_id_of_chunk(chunk_id)
        result_list.append({
            'id': chunk_id,
            'text': results['documents'][0][i],
            'distance': results['distances'][0][i],
            'group_id': group_id,
            'paper_id_list': [paper_id],
            'metadatas': results['metadatas'][0][i],
            'collection_name': COLLECTION_CHUNK,
        })

    return result_list


def query_summary_chunk(query_text, top_k=20, query_group_id=-1):
    client = chromadb.PersistentClient(path=get_db_path())
    collection = client.get_collection(name=COLLECTION_SUMMARY)

    results = collection.query(
        query_texts=[query_text],
        n_results=top_k,
        where=None if query_group_id == -1 else {'group_id': str(query_group_id)}
    )

    summary_list = get_all_summary_chunks()

    result_list = []
    for i in range(len(results['ids'][0])):
        summary_id = results['ids'][0][i]
        group_id = results['metadatas'][0][i]['group_id']

        from_base_chunk = results['metadatas'][0][i]['from_base_chunk']
        cur_chunk_id_list = json.loads(results['metadatas'][0][i]['chunk_id_list'])
        while not from_base_chunk:
            tmp_chunk_id_list = []
            for tmp_summary_id in cur_chunk_id_list:
                for summary in summary_list:
                    if summary['summary_id'] == tmp_summary_id:
                        tmp_chunk_id_list += summary['chunk_id_list']
                        from_base_chunk = summary['from_base_chunk']
                        break
            cur_chunk_id_list = list(set(tmp_chunk_id_list))

        paper_id_list = []
        for chunk_id in cur_chunk_id_list:
            tmp_paper_id, tmp_group_id = get_ref_id_of_chunk(chunk_id)
            if tmp_group_id == group_id:
                paper_id_list.append(tmp_paper_id)

        paper_id_list = list(set(paper_id_list))

        result_list.append({
            'id': summary_id,
            'text': results['documents'][0][i],
            'distance': results['distances'][0][i],
            'group_id': group_id,
            'paper_id_list': paper_id_list,
            'metadatas': results['metadatas'][0][i],
            'collection_name': COLLECTION_SUMMARY,
        })

    return result_list


def query_report_chunk(query_text, top_k=20, query_group_id=-1):
    client = chromadb.PersistentClient(path=get_db_path())
    collection = client.get_collection(name=COLLECTION_COMMUNITY_REPORT)

    results = collection.query(
        query_texts=[query_text],
        n_results=top_k,
        where=None if query_group_id == -1 else {'group_id': str(query_group_id)}
    )

    result_list = []
    for i in range(len(results['ids'][0])):
        report_id = results['ids'][0][i]

        chunk_id_list = json.loads(results['metadatas'][0][i]['chunk_id_list'])
        group_id = results['metadatas'][0][i]['group_id']

        paper_id_list = []
        for chunk_id in chunk_id_list:
            tmp_paper_id, tmp_group_id = get_ref_id_of_chunk(chunk_id)
            if tmp_group_id == group_id:
                paper_id_list.append(tmp_paper_id)

        result_list.append({
            'id': report_id,
            'text': results['documents'][0][i],
            'distance': results['distances'][0][i],
            'group_id': group_id,
            'paper_id_list': paper_id_list,
            'metadatas': results['metadatas'][0][i],
            'collection_name': COLLECTION_COMMUNITY_REPORT,
        })

    return result_list


def get_query_chunks(query_option, query_text, top_k=20, query_group_id=-1):
    need_base_chunk = False
    need_summary_chunk = False
    need_report_chunk = False

    if query_option == 1:
        # 1. GraphRAG + Raptor: community report + summary
        need_base_chunk = False
        need_summary_chunk = True
        need_report_chunk = True
    elif query_option == 2:
        # 2. Raptor: base + summary
        need_base_chunk = True
        need_summary_chunk = True
        need_report_chunk = False
    elif query_option == 3:
        # 3. GraphRAG: community report
        need_base_chunk = False
        need_summary_chunk = False
        need_report_chunk = True
    elif query_option == 4:
        # 4. Base only: base
        need_base_chunk = True
        need_summary_chunk = False
        need_report_chunk = False

    base_chunk_list = query_base_chunk(query_text, top_k, query_group_id) if need_base_chunk else []
    summary_chunk_list = query_summary_chunk(query_text, top_k, query_group_id) if need_summary_chunk else []
    report_chunk_list = query_report_chunk(query_text, top_k, query_group_id) if need_report_chunk else []

    chunk_list = base_chunk_list + summary_chunk_list + report_chunk_list
    chunk_list.sort(key=lambda x: x['distance'], reverse=False)

    return chunk_list[:top_k]


def get_db_path():
    try:
        with open(DB_TMP_FILE_PATH, 'r') as f:
            db_path = f.read()
        db_path = db_path.strip()
        if not db_path:
            db_path = DATABASE_PATH
    except:
        db_path = DATABASE_PATH
    return db_path


def update_db_path(new_db_path):
    with open(DB_TMP_FILE_PATH, 'w') as f:
        f.write(new_db_path)
        f.flush()


def rm_db_tmp_file():
    if os.path.isfile(DB_TMP_FILE_PATH):
        os.remove(DB_TMP_FILE_PATH)


def get_group_id_by_tmp_file():
    try:
        with open(GROUP_ID_TMP_FILE_PATH, 'r') as f:
            group_id = f.read()
        group_id = group_id.strip()
    except:
        group_id = ''
    return group_id


def update_group_id_tmp_file(group_id):
    with open(GROUP_ID_TMP_FILE_PATH, 'w') as f:
        f.write(group_id)
        f.flush()


def rm_group_id_tmp_file():
    if os.path.isfile(GROUP_ID_TMP_FILE_PATH):
        os.remove(GROUP_ID_TMP_FILE_PATH)


def check_group_id(group_id):
    group_exist = False
    for group in get_all_groups():
        if group['group_id'] == str(group_id):
            group_exist = True
            break
    return group_exist


def split_text_into_chunks(text, min_num_char=1000):
    # Split the text by double newline (blank lines)
    paragraphs = text.split('\n\n')

    chunks = []
    current_chunk = []
    current_length = 0

    for paragraph in paragraphs:
        paragraph_length = len(paragraph)

        if current_length >= min_num_char and current_chunk:
            # If the current chunk already meets the minimum length requirement, finish the chunk
            chunks.append('\n\n'.join(current_chunk))
            current_chunk = [paragraph]
            current_length = paragraph_length
        else:
            # Otherwise, add the paragraph to the current chunk
            current_chunk.append(paragraph)
            current_length += paragraph_length

    # Add the last chunk if it exists
    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))

    return chunks


def split_text_into_sub_chunks(denoised_text, min_num_tokens=300):
    denoised_text = denoised_text.strip()

    blank_line = '\n\n'
    bullet = '#'
    join_by = blank_line

    paragraphs = denoised_text.split(blank_line)
    if len(paragraphs) > 1:
        points = paragraphs
    else:
        points = [bullet + p for p in denoised_text.split(bullet) if p]
        join_by = ''

    sub_chunks = []
    current_chunk = []
    current_length = 0

    for point in points:
        point_tokens = len(word_tokenize(point))

        if current_length >= min_num_tokens and current_chunk:
            sub_chunks.append(join_by.join(current_chunk))
            current_chunk = [point]
            current_length = point_tokens
        else:
            current_chunk.append(point)
            current_length += point_tokens

    if current_chunk:
        sub_chunks.append(join_by.join(current_chunk))

    return sub_chunks


def get_chunks_for_graphrag(text):
    paper_id = get_id(COLLECTION_PAPER, text)
    chunks = []
    for chunk in get_all_chunks():
        if chunk['paper_id'] == paper_id:
            if len(chunk['sub_chunks']) > 0:
                chunks += chunk['sub_chunks']
            else:
                chunks.append(chunk['chunk_content'])
    return chunks


def get_ref_ids_for_group(group_id):
    group_id = str(group_id)

    paper_id_list = []
    for paper in get_all_papers():
        if paper['group_id'] == group_id:
            paper_id_list.append(paper['paper_id'])
    paper_id_list = list(set(paper_id_list))

    chunk_id_list = []
    for chunk in get_all_chunks():
        if chunk['paper_id'] in paper_id_list or chunk['group_id'] == group_id:
            chunk_id_list.append(chunk['chunk_id'])
    chunk_id_list = list(set(chunk_id_list))

    report_id_list = []
    for report in get_all_community_reports():
        for chunk_id in report['chunk_id_list']:
            if chunk_id in chunk_id_list:
                report_id_list.append(report['report_id'])
                break
    report_id_list = list(set(report_id_list))

    summary_id_list = []
    for summary in get_all_summary_chunks():
        if summary['group_id'] == group_id:
            summary_id_list.append(summary['summary_id'])
    summary_id_list = list(set(summary_id_list))

    relationship_id_list = []
    for relationship in get_all_relationships():
        if relationship['chunk_id'] in chunk_id_list:
            relationship_id_list.append(relationship['relationship_id'])
    relationship_id_list = list(set(relationship_id_list))

    return paper_id_list, chunk_id_list, relationship_id_list, report_id_list, summary_id_list


def count_ref_ids_for_group(group_id):
    paper_id_list, chunk_id_list, relationship_id_list, report_id_list, summary_id_list = get_ref_ids_for_group(group_id)

    group_name = get_group_name(group_id)

    print(f'Group ID: {group_id}, Group Name: {group_name}')
    print('Number of paper:', len(paper_id_list))
    print('Number of chunk:', len(chunk_id_list))
    print('GraphRAG:')
    print('Number of relationship:', len(relationship_id_list))
    print('Number of community report:', len(report_id_list))
    print('Raptor:')
    print('Number of summary:', len(summary_id_list))


def delete_items(collection_name: str, ids: list):
    try:
        client = chromadb.PersistentClient(path=get_db_path())
        collection = client.get_collection(name=collection_name)
        collection.delete(ids=ids)
    except:
        pass


def delete_group(group_id, del_graphrag=True, del_raptor=True):
    group_id = str(group_id)
    group_exist = False
    for group in get_all_groups():
        if group['group_id'] == group_id:
            group_exist = True
            break

    if not group_exist:
        print(f'Group ID {group_id} does not exist.')
        return

    print(f'Group ID: {group_id}, Delete GraphRAG: {del_graphrag}, Delete Raptor: {del_raptor}')
    print('Before:')
    count_ref_ids_for_group(group_id)

    paper_id_list, chunk_id_list, relationship_id_list, report_id_list, summary_id_list = get_ref_ids_for_group(group_id)

    if del_graphrag:
        if relationship_id_list:
            delete_items(COLLECTION_RELATIONSHIP, relationship_id_list)
        if report_id_list:
            delete_items(COLLECTION_COMMUNITY_REPORT, report_id_list)

    if del_raptor:
        if summary_id_list:
            delete_items(COLLECTION_SUMMARY, summary_id_list)

    if del_graphrag and del_raptor:
        delete_items(COLLECTION_GROUP, [group_id])
        if paper_id_list:
            delete_items(COLLECTION_PAPER, paper_id_list)
        if chunk_id_list:
            delete_items(COLLECTION_CHUNK, chunk_id_list)

    print('After:')
    count_ref_ids_for_group(group_id)


def get_group_name(group_id):
    group_name = ''
    for group in get_all_groups():
        if group['group_id'] == str(group_id):
            group_name = group['group_name']
            break
    return group_name


def get_paper_name(paper_id, with_suffix=False):
    paper_name = ''
    for paper in get_all_papers():
        if paper['paper_id'] == str(paper_id):
            paper_name = paper['paper_name']
            break
    if not with_suffix:
        paper_name = os.path.splitext(paper_name)[0]
    return paper_name
