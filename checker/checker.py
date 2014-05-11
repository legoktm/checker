#! /usr/bin/env python
# Public domain; MZMcBride, 2011; Legoktm, 2014

from flask import Flask, request
import cgi
import urllib
import re
import oursql
import operator
import json
import os

app = Flask(__name__)

my_cnf = os.path.expanduser('~/replica.my.cnf')


def database_list():
    conn = oursql.connect(host='enwiki.labsdb',
                          db='meta_p',
                          read_default_file=my_cnf)
    cursor = conn.cursor()
    cursor.execute('''
    /* checker.py database_list */
    SELECT
      dbname
    FROM wiki
    WHERE is_closed = 0;
    ''')
    databases = cursor.fetchall()
    cursor.close()
    conn.close()
    return [database[0] for database in databases]


def choose_host_and_domain(db):
    conn = oursql.connect(host='enwiki.labsdb',
                          db='meta_p',
                          read_default_file=my_cnf)
    cursor = conn.cursor()
    cursor.execute('''
    /* checker.py choose_host_and_domain */
    SELECT
      url
    FROM wiki
    WHERE dbname = ?;
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


def get_extension_namespaces(domain):
    params = {
        'action': 'query',
        'meta': 'proofreadinfo|siteinfo',
        'piprop': 'namespaces',
        'siprop': 'namespaces',
        'format': 'json'
    }
    query_url = '%s/w/api.php?%s' % (domain, urllib.urlencode(params))
    url_contents = urllib.urlopen(query_url).read()
    parsed_content = json.loads(url_contents)
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
      pl_title
    FROM pagelinks
    JOIN page AS p1
    ON pl_from = p1.page_id
    JOIN page AS p2
    ON p2.page_title = pl_title
    AND p2.page_namespace = pl_namespace
    WHERE pl_namespace = ?
    AND p1.page_namespace = ?
    AND p1.page_title = ?;
    ''', (page_namespace, index_namespace, index_page))
    for row in cursor.fetchall():
        pl_title = row[0]
        try:
            sort_key = int(unicode(row[0].rsplit('/', 1)[1].decode('utf-8')))
        except IndexError:
            sort_key = 1
        page_links.append([pl_title, sort_key])
    return page_links

def get_page_status(cursor, db, page_namespace, page):
    page_status = {}
    # Check if the page has transclusions first
    cursor.execute('''
    /* checker.py get_page_status */
    SELECT
      COUNT(*)
    FROM templatelinks
    WHERE tl_namespace = ?
    AND tl_title = ?;
    ''', (page_namespace, page.decode('utf-8')))
    transclusion_count = cursor.fetchall()
    if transclusion_count:
        page_status['transclusion_count'] = transclusion_count[0]
    # Then check if the page has been proofread
    cursor.execute('''
    /* checker.py get_page_status */
    SELECT
      cl_to
    FROM page
    JOIN categorylinks
    ON cl_from = page_id
    WHERE page_id = cl_from
    AND page_namespace = ?
    AND page_title = ?;
    ''', (page_namespace, page.decode('utf-8')))
    proofread_status = cursor.fetchall()
    if proofread_status:
        page_status['proofread_status'] = proofread_status[0][0].lower().replace('_', ' ')
    return page_status


@app.route('/')
def main():
    TEXT = ''
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

    yes_table = '''\
<table id="ck-yes-table">
%s
</table>'''
    yes_rows = []

    no_table = '''\
<table id="ck-no-table">
%s
</table>'''
    no_rows = []

    tables = []
    if host is not None and title and extension_dict:
        conn = oursql.connect(host=host, db=db+'_p', read_default_file=my_cnf)
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
                sort_key = item[1]
                status = get_page_status(cursor, db+'_p', page_namespace_id, page_link)
                table_row = '''\
<tr>
<td>
<a href="%s/wiki/%s">%s</a>\
</td>
<td>
%s
</td>
</tr>''' % (domain,
            '%s:%s' % (urllib.quote(page_namespace_name.encode('utf-8')),
                       urllib.quote(page_link.decode('utf-8').encode('utf-8'))),
            cgi.escape('%s:%s' % (page_namespace_name, page_link.decode('utf-8').replace('_', ' ')), quote=True),
            status['proofread_status'].decode('utf-8'))
                if status['transclusion_count'] > 0:
                    yes_rows.append(table_row)
                else:
                    no_rows.append(table_row)
        tables.append(yes_rows)
        tables.append(no_rows)
        cursor.close()
        conn.close()

    TEXT += '''\
<!doctype html>
<html>
<head>
<meta http-equiv="Content-Type" content="text/html;charset=utf-8">
<link rel="stylesheet" href="/checker/static/style-checker.css" type="text/css" />
<script type="text/javascript" src="/checker/static/jquery-1.3.2.min.js"></script>
<script type="text/javascript" src="/checker/static/jquery.tablesorter.js"></script>
<script type="text/javascript">
var extracted_data = function(node)
{
        var text = node.innerText || node.textContent
        return text.substring(text.lastIndexOf('/')+1);
}
$(document).ready(function()
{
        $('input.focus').focus();
        $('.ck-results').tablesorter(
                {widgets: ['zebra'],
                 textExtraction: extracted_data}
        );
}
);
</script>
<title>checker</title>
</head>
<body>
<div class="header" id="main-title"><a href="/checker/" title="checker">checker</a></div>\
'''

    if title:
        if db and host is not None and title and extension_dict:
            TEXT += '<div id="ck-tables-wrapper">'
            count = 0
            for table in tables:
                if count == 0:
                    TEXT += '<h1 class="header" id="Transcluded"> Transcluded </h1>'
                else:
                    TEXT += '<h1 class="header" id="Not transcluded"> Not transcluded </h1>'
                TEXT += '''\
<table class="ck-results inner-table">
<thead>
<tr>
<th class="header" id="ck-page-column">Page</th>
<th class="header" id="ck-status-column">Status</th>
</tr>
</thead>
<tbody>
%s
</tbody>
</table>''' % ('\n'.join(table))
                count += 1
            TEXT += '</div>'
        else:
            TEXT += '''\
<pre>
There was some sort of error. Sorry. :-(
</pre>'''

    elif host is None:
        TEXT += '''\
<pre>
You didn't specify an appropriate database name.
</pre>'''

    else:
        TEXT += '''\
<form action="/checker/" method="get">
<table id="input" class="inner-table">
<tr>
<th colspan="2" class="header">Input index title below.</th>
</tr>
<tr>
<th>Database</th>
<th>
<select id="database" name="db">'''
        for i in database_list():
            if i == '%s' % db:
                TEXT += '''\
<option value="%s" selected="selected">%s</option>''' % (i, i)
            else:
                TEXT += '''\
<option value="%s">%s</option>''' % (i, i)
        TEXT += '''\
</select>
</th>
</tr>
<tr>
<td colspan="2" id="input-cell">
<input class="focus" id="input" name="title" size="50" /><input id="go-button" type="submit" value="Go" />
</td>
</tr>
</table>
</form>'''

    TEXT += '''\
<div id="footer">
<div id="meta-info">
<a href="https://github.com/legoktm/checker" title="source code">public domain</a>&nbsp;<b>&middot;</b>&nbsp;\
<a href="http://en.wikipedia.org/w/index.php?title=User_talk:MZMcBride&amp;action=edit&amp;section=new" title="Report a bug">bugs</a>
</div>
</div>
</body>
</html>
'''
    return TEXT

if __name__ == '__main__':
    app.run(debug=True)
