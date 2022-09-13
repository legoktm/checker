#! /usr/bin/env python
# Public domain; MZMcBride, 2011; Legoktm, 2014

from flask import Flask, request, render_template
from flask_caching import Cache
import re
import requests
import operator
import toolforge

app = Flask(__name__)
cache = Cache(
    app,
    config={'CACHE_TYPE': 'redis',
            'CACHE_REDIS_HOST': 'tools-redis',
            'CACHE_KEY_PREFIX': 'tool-checker'}
)
toolforge.set_user_agent('checker')


@cache.cached(timeout=60*60*24)
def database_list():
    conn = toolforge.connect('meta_p')
    cursor = conn.cursor()
    cursor.execute('''
    /* checker.py database_list */
    SELECT
      dbname
    FROM wiki
    WHERE is_closed = 0
    ORDER BY dbname ASC;
    ''')
    databases = cursor.fetchall()
    cursor.close()
    conn.close()
    ret = []
    for database in databases:
        ret.append(database[0])
    return ret


@cache.memoize(timeout=60*60*24)
def choose_host_and_domain(db):
    conn = toolforge.connect('meta_p')
    cursor = conn.cursor()
    cursor.execute('''
    /* checker.py choose_host_and_domain */
    SELECT
      url
    FROM wiki
    WHERE dbname = %s;
    ''', (db,))
    result = cursor.fetchall()
    cursor.close()
    conn.close()
    if result:
        for row in result:
            domain = '%s' % row[0]
        if domain:
            return {'host': db + '.labsdb', 'domain': domain}
    return None


@cache.memoize(timeout=60*60*24)
def get_extension_namespaces(domain):
    params = {
        'action': 'query',
        'meta': 'proofreadinfo|siteinfo',
        'piprop': 'namespaces',
        'siprop': 'namespaces',
        'format': 'json'
    }
    query_url = '%s/w/api.php' % domain
    req = requests.get(query_url, params=params)
    parsed_content = req.json()
    try:
        page_namespace = parsed_content['query']['proofreadnamespaces']['page']['id']
        index_namespace = parsed_content['query']['proofreadnamespaces']['index']['id']
    except KeyError:
        return None
    names = parsed_content['query']['namespaces']
    return {'page_namespace': page_namespace,
            'index_namespace': index_namespace,
            'names': names}


def get_page_links(cursor, db, page_namespace, index_namespace, index_page):
    page_links = []
    cursor.execute('''
    /* checker.py get_page_links */
    SELECT
      lt_title
    FROM templatelinks
    JOIN linktarget
    ON tl_target_id = lt_id
    JOIN page AS p1
    ON tl_from = p1.page_id
    JOIN page AS p2
    ON p2.page_title = lt_title
    AND p2.page_namespace = lt_namespace
    WHERE lt_namespace = %s
    AND p1.page_namespace = %s
    AND p1.page_title = %s;
    ''', (page_namespace, index_namespace, index_page))
    for row in cursor.fetchall():
        tl_title = row[0].decode()
        try:
            sort_key = int(row[0].decode().rsplit('/', 1)[1])
        except IndexError:
            sort_key = 1
        page_links.append([tl_title, sort_key])
    return page_links


def get_page_status(cursor, db, page_namespace, page):
    page_status = {}
    # Check if the page has transclusions first
    cursor.execute('''
    /* checker.py get_page_status */
    SELECT
      COUNT(*)
    FROM templatelinks
    JOIN linktarget
    ON tl_target_id = lt_id
    JOIN page
    ON tl_from = page_id
    WHERE lt_namespace = %s
    AND lt_title = %s
    AND page_namespace = 0;
    ''', (page_namespace, page))
    transclusion_count = cursor.fetchall()
    if transclusion_count:
        page_status['transclusion_count'] = int(transclusion_count[0][0])
    # Then check if the page has been proofread
    cursor.execute('''
    /* checker.py get_page_status */
    SELECT
      cl_to
    FROM page
    JOIN categorylinks
    ON cl_from = page_id
    WHERE page_id = cl_from
    AND page_namespace = %s
    AND page_title = %s;
    ''', (page_namespace, page))
    proofread_status = cursor.fetchall()
    if proofread_status:
        page_status['proofread_status'] = proofread_status[0][0].decode().lower().replace('_', ' ')
    return page_status


@app.route('/')
def main():
    host = db = domain = extension_dict = None
    # Pick a db; make enwikisource the default
    if request.args.get('db') is not None:
        db = request.args.get('db').replace('_p', '')
    else:
        db = 'enwikisource'

    # All right, now let's pick a host and domain
    connection_props = choose_host_and_domain(db)
    if connection_props:
        host = connection_props['host']
        domain = connection_props['domain']
        if domain:
            extension_dict = get_extension_namespaces(domain)
        if extension_dict:
            page_namespace_id = extension_dict['page_namespace']
            index_namespace_id = extension_dict['index_namespace']
            page_namespace_name = extension_dict['names'][str(page_namespace_id)]['*']
            index_namespace_name = extension_dict['names'][str(index_namespace_id)]['*']

    if 'title' in request.args:
        title = request.args.get('title')
    else:
        title = ''

    yes_rows = []

    no_rows = []

    error = None
    if host is not None and title and extension_dict:
        conn = toolforge.connect(db)
        cursor = conn.cursor()
        # Eliminate LTR and RTL marks and strip extra whitespace.
        title = re.sub(r'(\xe2\x80\x8e|\xe2\x80\x8f)', '', title).strip(' ')
        # Prep the title for the query (replace spaces and strip namespace name if present).
        clean_title = title.replace(' ', '_').split(index_namespace_name+':', 1)[1]
        page_links = get_page_links(cursor, db+'_p', page_namespace_id, index_namespace_id, clean_title)
        if page_links:
            # Sort!
            page_links = sorted(page_links, key=operator.itemgetter(1))
            for item in page_links:
                page_link = item[0]
                status = get_page_status(cursor, db+'_p', page_namespace_id, page_link)
                table_row = {
                    'domain': domain,
                    'ns': page_namespace_name,
                    'title': page_link,
                    'status': status['proofread_status']
                }
                if status['transclusion_count'] > 0:
                    yes_rows.append(table_row)
                else:
                    no_rows.append(table_row)
        cursor.close()
        conn.close()

    show_form = False
    if title:
        if not (db and host is not None and title and extension_dict):
            error = 'There was some sort of error. Sorry. :-('
    elif host is None:
        error = "You didn't specify an appropriate database name."
    else:
        show_form = True

    return render_template(
        'main.html',
        error=error,
        yes_rows=yes_rows,
        no_rows=no_rows,
        show_form=show_form,
        databases=database_list(),
        selected_db=db,
        clean=lambda x: x.replace('_', ' ')
    )


if __name__ == '__main__':
    app.run(debug=True)
