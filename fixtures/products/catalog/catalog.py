"""Small deterministic offline fixture, not a hosted application."""
import csv
from decimal import Decimal, InvalidOperation
import html
import json
from pathlib import Path
import sys


def read_items(source):
    try:
        reader = csv.DictReader(source, strict=True)
        if reader.fieldnames != ['id', 'name', 'price']:
            raise ValueError('Expected id,name,price')
        items, seen = [], set()
        for row in reader:
            if set(row) != {'id', 'name', 'price'} or not all(row.values()):
                raise ValueError('Incomplete item')
            price = Decimal(row['price'])
            if not price.is_finite() or price < 0 or price != price.quantize(Decimal('0.01')):
                raise ValueError('Invalid price')
            if row['id'] in seen:
                raise ValueError('Duplicate identifier')
            seen.add(row['id'])
            items.append({'id': row['id'], 'name': row['name'], 'price': format(price, '.2f')})
        if not items:
            raise ValueError('Empty catalog')
        return sorted(items, key=lambda item: (item['name'].casefold(), item['id']))
    except (csv.Error, InvalidOperation) as error:
        raise ValueError('Invalid catalog input') from error


def generate(source, output):
    items = read_items(source)
    entries = ''.join('<li data-id="' + html.escape(item['id'], quote=True) + '">' +
                      html.escape(item['name'], quote=True) + ' ' + item['price'] + '</li>' for item in items)
    page = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Catalog</title><h1>Synthetic catalog</h1><ul>' + entries + '</ul></html>'
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'index.html').write_text(page, encoding='utf-8')
    (destination / 'catalog.json').write_text(json.dumps(items), encoding='utf-8')
    return items


if __name__ == '__main__':
    with open(sys.argv[1], encoding='utf-8', newline='') as source:
        generate(source, sys.argv[2])
