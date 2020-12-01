# -*- coding: utf-8 -*-
import json
import logging
import base64
import hashlib
import datetime
import tldextract
import xml.etree.ElementTree as etree
from lib.cdx import CdxIndex

logger = logging.getLogger(__name__)

class GenerateW3ACTTitleExport(luigi.Task):
    task_namespace = 'discovery'
    date = luigi.DateParameter(default=datetime.date.today())

    record_count = 0
    blocked_record_count = 0
    missing_record_count = 0
    embargoed_record_count = 0

    target_count = 0
    collection_count = 0
    collection_published_count = 0
    subject_count = 0
    subject_published_count = 0

    def requires(self):
        return [TargetList(self.date), CollectionList(self.date), SubjectList(self.date)]

    def output(self):
        logger.warning('in output')
        return state_file(self.date,'access-data', 'title-level-metadata-w3act.xml')

    def run(self):
        # Get the data:
        targets = json.load(self.input()[0].open())
        self.target_count = len(targets)
        collections = json.load(self.input()[1].open())
        self.collection_count = len(collections)
        subjects = json.load(self.input()[2].open())
        self.subject_count = len(subjects)

        # Index collections by ID:
        collections_by_id = {}
        for col in collections:
            collections_by_id[int(col['id'])] = col
            if col['publish']:
                self.collection_published_count += 1

        # Index subjects by ID:
        subjects_by_id = {}
        for sub in subjects:
            subjects_by_id[int(sub['id'])] = sub
            if sub['publish']:
                self.subject_published_count += 1

        # Convert to records:
        records = []
        for target in targets:
            # Skip blocked items:
            if target['crawl_frequency'] == 'NEVERCRAWL':
                logger.warning("The Target '%s' is blocked (NEVERCRAWL)." % target['title'])
                self.blocked_record_count += 1
                continue
            # Skip items that have no crawl permission?
            # hasOpenAccessLicense == False, and inScopeForLegalDeposit == False ?
            # Skip items with no URLs:
            if len(target.get('urls',[])) == 0:
                logger.warning("Skipping %s" % target.get('title', 'NO TITLE'))
                continue
            # Get the url, use the first:
            url = target['urls'][0]
            # Extract the domain:
            parsed_url = tldextract.extract(url)
            publisher = parsed_url.registered_domain
            # Lookup in CDX:
            wayback_date_str = CdxIndex().get_first_capture_date(url) # Get date in '20130401120000' form.
            if wayback_date_str is None:
                logger.warning("The URL '%s' is not yet available, inScopeForLegalDeposit = %s" % (url, target['isNPLD']))
                self.missing_record_count += 1
                continue
            wayback_date = datetime.datetime.strptime(wayback_date_str, '%Y%m%d%H%M%S')
            first_date = wayback_date.isoformat()

            # Honour embargo
            ago = datetime.datetime.now() - wayback_date
            if ago.days <= 7:
                self.embargoed_record_count += 1
                continue

            #### Otherwise, build the record:
            record_id = "%s/%s" % (wayback_date_str, base64.b64encode(hashlib.md5(url.encode('utf-8')).digest()))
            title = target['title']
            # set the rights and wayback_url depending on licence
            if target.get('isOA', False):
                rights = '***Free access'
                wayback_url = 'https://www.webarchive.org.uk/wayback/archive/' + wayback_date_str + '/' + url
            else:
                rights = '***Available only in our Reading Rooms'
                wayback_url = 'https://bl.ldls.org.uk/welcome.html?' + wayback_date_str + '/' + url
            rec = {
                'id': record_id,
                'date': first_date,
                'url': url,
                'title': title,
                'rights': rights,
                'publisher': publisher,
                'wayback_url': wayback_url
            }
            # Add any collection:
            if len(target['subject_ids']) > 0:
                sub0 = subjects_by_id.get(int(target['subject_ids'][0]), {})
                rec['subject'] = sub0.get('name', None)

            # And append record to the set:
            records.append(rec)
            self.record_count += 1

        # declare output XML namespaces
        OAINS = 'http://www.openarchives.org/OAI/2.0/'
        OAIDCNS = 'http://www.openarchives.org/OAI/2.0/oai_dc/'
        DCNS = 'http://purl.org/dc/elements/1.1/'
        XLINKNS = 'http://www.w3.org/1999/xlink'
        OAIDC_B = "{%s}" % OAIDCNS
        DC_B = "{%s}" % DCNS
        XLINK_B = "{%s}" % XLINKNS

        # create OAI-PMH XML via lxml
        oaiPmh = etree.Element('OAI-PMH', nsmap={None:OAINS, 'oai_dc':OAIDCNS, 'dc':DCNS, 'xlink':XLINKNS})
        listRecords = etree.SubElement(oaiPmh, 'ListRecords')

        for rec in records:
            record = etree.SubElement(listRecords, 'record')

            # header
            header = etree.SubElement(record, 'header')
            identifier = etree.SubElement(header, 'identifier')
            identifier.text = rec['id']

            # metadata
            metadata = etree.SubElement(record, 'metadata')
            dc = etree.SubElement(metadata, OAIDC_B+'dc')
            source = etree.SubElement(dc, DC_B+'source' )
            source.text = rec['url']
            publisher = etree.SubElement(dc, DC_B+'publisher' )
            publisher.text = rec['publisher']
            title = etree.SubElement(dc, DC_B+'title' )
            title.text = rec['title']
            date = etree.SubElement(dc, DC_B+'date' )
            date.text = rec['date']
            rights = etree.SubElement(dc, DC_B+'rights' )
            rights.text = rec['rights']
            href = etree.SubElement(dc, XLINK_B+'href' )
            href.text = rec['wayback_url']

            if 'subject' in rec:
                subject = etree.SubElement(dc, DC_B+'subject')
                subject.text = rec['subject']

        # output OAI-PMH XML
        with self.output().open('w') as f:
            f.write(etree.tostring(oaiPmh, xml_declaration=True, encoding='UTF-8', pretty_print=True))


    def get_metrics(self, registry):
        # type: (CollectorRegistry) -> None

        g = Gauge('ukwa_record_count',
                  'Total number of UKWA records.',
                    labelnames=['kind', 'status'], registry=registry)

        g.labels(kind='targets', status='_any_').set(self.target_count)
        g.labels(kind='collections', status='_any_').set(self.collection_count)
        g.labels(kind='collections', status='published').set(self.collection_published_count)
        g.labels(kind='subjects', status='_any_').set(self.subject_count)

        g.labels(kind='title_level', status='complete').set(self.record_count)
        g.labels(kind='title_level', status='blocked').set(self.blocked_record_count)
        g.labels(kind='title_level', status='missing').set(self.missing_record_count)
        g.labels(kind='title_level', status='embargoed').set(self.embargoed_record_count)
