# -*- coding: utf-8; -*-
#
# This file is part of Superdesk.
#
# Copyright 2013, 2014 Sourcefabric z.u. and contributors.
#
# For the full copyright and license information, please see the
# AUTHORS and LICENSE files distributed with this source code, or
# at https://www.sourcefabric.org/superdesk/license

import logging
from superdesk.io.registry import register_feed_parser
from superdesk.io.feed_parsers import XMLFeedParser
from email.utils import parsedate_to_datetime
from superdesk import etree as sd_etree
from superdesk import get_resource_service
from superdesk.upload import url_for_media
from xml.sax.saxutils import quoteattr
from lxml import etree
import copy
from collections import OrderedDict

logger = logging.getLogger(__name__)
nsmap = {
    "excerpt": "http://wordpress.org/export/1.2/excerpt/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "wfw": "http://wellformedweb.org/CommentAPI/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "wp": "http://wordpress.org/export/1.2/",
}
embed_TPL = "EMBED {} Image {{id: \"{}\"}}"


class WPWXRFeedParser(XMLFeedParser):
    """
    Feed Parser for Wordpress' WXR format
    """

    NAME = 'wpwxr'

    label = 'Wordpress WXR parser'

    def __init__(self):
        super().__init__()
        # we use OrderedDict to have control on parsing order
        self.default_mapping = OrderedDict([
            ('guid', 'guid'),
            ('item_id', 'guid'),
            ('versioncreated', {'xpath': 'pubDate/text()',
                                'filter': parsedate_to_datetime}),
            ('author', 'dc:creator'),
            ('headline', 'title'),
            # images are handled in body_hook
            ('body_html', {'xpath': 'content:encoded',
                           'key_hook': self.body_hook}),
            ('keywords', {'xpath': 'category[@domain="post_tag"]',
                          'list': True}),
            ('anpa_category', {'xpath': 'category[@domain="category"]/text()',
                               'list': True,
                               'filter': lambda cat: {'qcode': cat, 'qname': cat}}),
            ('attachments', {'xpath': 'wp:attachment_url',
                             'list': True,
                             'key_hook': self.attachments_hook})])

    def can_parse(self, xml):
        return xml.tag == 'rss'

    def parse(self, xml, provider=None):
        return list(self.parse_items(xml))

    def parse_items(self, xml):
        for item_xml in xml.findall('channel/item', namespaces=nsmap):
            yield self.parse_item(item_xml)

    def parse_item(self, item_xml):
        item = {}
        self.do_mapping(item, item_xml, namespaces=nsmap)
        if "associations" in item:
            for _, data in item['associations'].items():
                # these 3 fields are mandatory in default setup
                # we use a space for that to avoid issue when publishing
                data.setdefault('headline', item['headline'])
                data.setdefault('alt_text', ' ')
                data.setdefault('description_text', ' ')
        return item

    def _add_image(self, item, url):
        associations = item.setdefault('associations', {})
        upload_service = get_resource_service("upload")
        object_id = upload_service.post([{'URL': url}])[0]
        media_data = next(upload_service.get(req=None, lookup={'_id': object_id}))
        data = {
            '_id': media_data['_id'],
            'type': 'picture',
            'ingest_provider': self.NAME,
            'renditions': copy.deepcopy(media_data['renditions'])
        }

        # we use featuremedia for the first image, then embeddedX
        if 'featuremedia' not in associations:
            key = 'featuremedia'
        else:
            key = 'embedded' + str(len(associations) - 1)

        associations[key] = data
        return key, media_data

    def body_hook(self, item, html):
        """Copy content to body_html

        if img are found in the content, they are uploaded.
        First image is used as feature media, then there are embeds
        """
        if "img" in html:
            content = sd_etree.parse_html(html, 'html')
            for img in content.xpath('//img'):
                src = img.get('src')
                try:
                    key, media_data = self._add_image(item, src)
                except Exception as e:
                    logger.error(e)
                    img.getparent().remove(img)
                    continue
                _id = media_data['_id']
                url = url_for_media(_id)
                img.set("src", url)
                if key == 'featuremedia':
                    # no need to embed the image for featuremedia
                    continue
                embed_start = etree.Comment(embed_TPL.format('START', key))
                embed_end = etree.Comment(embed_TPL.format('END', key))
                img.addprevious(embed_start)
                img.addnext(embed_end)

            html = etree.tostring(content, encoding="unicode")

        item['body_html'] = html

    def attachments_hook(self, item, attachments):
        """Attachment are parsed at the end

        if it's the first image found, it's used as feature media
        else it's used as embed and put at the end of body_html
        """
        for url in attachments:
            try:
                key, media_data = self._add_image(item, url)
            except Exception as e:
                logger.error(e)
                continue
            if key == 'featuremedia':
                # no need to embed the image for featuremedia
                continue
            embed_start = "<!--" + embed_TPL.format('START', key) + "-->"
            embed_end = "<!--" + embed_TPL.format('END', key) + "-->"
            _id = media_data['_id']
            new_url = url_for_media(_id)
            img = '<img src={src} height="{height}" width="{width}">'.format(
                src=quoteattr(new_url),
                height=media_data['renditions']['original']['height'],
                width=media_data['renditions']['original']['width'])
            item['body_html'] += '<div>' + embed_start + img + embed_end + '</div>'


register_feed_parser(WPWXRFeedParser.NAME, WPWXRFeedParser())
