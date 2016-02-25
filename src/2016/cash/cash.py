import io
import json
import re
import sys
from collections import namedtuple, deque
from lxml import etree
from lxml.cssselect import CSSSelector

PAY = "Pay"
DED = "Deduction"
TAX = "Tax"

def parse_mypay_html(filename):
    pay = etree.parse(filename, etree.HTMLParser())
    for check in CSSSelector('.payStatement')(pay):
        assert len(check) == 1
        tbody = check[0]
        assert tbody.tag == "tbody"

        assert len(tbody) == 9
        assert len(tbody[0]) == 5 # blank columns

        assert len(tbody[1]) == 1 # logo
        assert tbody[1][0].attrib["colspan"] == "5"
        assert tbody[1][0][1].attrib["id"] == "companyLogo"

        assert len(tbody[2]) == 2 # address, summary table
        assert len(tbody[2][1]) == 1
        assert tbody[2][1][0].tag == "table"
        assert len(tbody[2][1][0]) == 1
        inf = tbody[2][1][0][0]
        assert inf.tag == "tbody"
        assert len(inf) == 6
        assert inf[3][0][0].text == "Pay date"
        paydate = inf[3][1][0].text
        assert inf[4][0][0].text == "Document"
        docid = inf[4][1][0].text
        assert inf[5][0][0].text == "Net pay"
        netpay = inf[5][1][0].text

        assert len(tbody[3]) == 1
        assert tbody[3][0].attrib["colspan"] == "5"
        assert tbody[3][0][0][0].text == "Pay details"
        assert len(tbody[4]) == 5

        netpay = parse_price(netpay)
        print(paydate, docid, netpay)

        assert len(tbody[5]) == 2
        assert tbody[5][0].attrib["colspan"] == "2"
        assert tbody[5][0].attrib["rowspan"] == "2"
        assert tbody[5][0][0][0].text == "Earnings"
        total = 0
        for label, details, current in tab(tbody[5][0], docid, PAY):
            current = parse_price(current, True)
            total += current
            print("  {}: {}{} -- {}".format(PAY, label, details, current))

        assert tbody[5][1].attrib["colspan"] == "3"
        assert tbody[5][1][0][0].text == "Deductions"
        for label, details, current in tab(tbody[5][1], docid, DED):
            current = parse_price(current, True)
            total -= current
            print("  {}: {}{} -- {}".format(DED, label, details, current))

        assert len(tbody[6]) == 1
        assert tbody[6][0].attrib["colspan"] == "3"
        assert tbody[6][0][0][0].text == "Taxes"
        for label, details, current in tab(tbody[6][0], docid, TAX):
            current = parse_price(current, True)
            total -= current
            print("  {}: {}{} -- {}".format(TAX, label, details, current))

        assert total == netpay
        assert len(tbody[8]) == 1
        assert tbody[8][0][0][0].text == "Pay summary"

def tab(el, docid, table_type):
    if table_type == PAY:
        if docid.startswith("RSU"):
            expected_cols = ['Pay type', 'Hours', 'Pay rate', 'Piece units',
                             'Piece Rate', 'current', 'YTD']
        else:
            expected_cols = ['Pay type', 'Hours', 'Pay rate', 'current', 'YTD']
    elif table_type == DED:
        expected_cols = ['Employee', 'Employer', 'Deduction', 'current', 'YTD',
                         'current', 'YTD']
    else:
        assert table_type == TAX
        expected_cols = ['Taxes', 'Based on', 'current', 'YTD']

    grids = CSSSelector("table.grid")(el)
    assert len(grids) == 1
    grid = grids[0]
    assert len(grid) == 2
    assert grid[0].tag == "thead"
    headcols = []
    for headrow in grid[0]:
        for headcol in headrow:
            if len(headcol) and headcol[0].tag != "img":
                headcols.append(headcol[0].text)
    assert expected_cols == headcols

    assert grid[1].tag == "tbody"
    for bodyrow in grid[1]:
        bodycols = []
        for colno, bodycol in enumerate(bodyrow):
            if colno == 0:
                bodycols.append(bodycol[0].attrib["data-title"])
            else:
                assert len(bodycol) == 0
                bodycols.append(bodycol.text)
        details = ""
        if table_type == PAY:
            if docid.startswith("RSU"):
                label, hours, rate, piece_units, piece_rate, current, ytd = \
                    bodycols
                assert piece_units == "0.000000"
                assert piece_rate == "$0.00"
            else:
                label, hours, rate, current, ytd = bodycols
            if hours != "0.0000" or rate != "$0.0000":
                details += " ({} hours, {}/hour)".format(hours, rate)
        elif table_type == DED:
            label, current, ytd, goog_current, goog_ytd, garbage = bodycols
            if goog_current != "$0.00":
                if label in ("401K Pretax", "Pretax 401 Flat",
                             "ER Benefit Cost"):
                    goog_401k = goog_current
                else:
                   assert False
            assert garbage == "\xa0"
        else:
            assert table_type == TAX
            label, income, current, ytd, garbage = bodycols
            assert garbage == "\xa0"
        if current != "$0.00":
          yield label, details, current

def parse_price(price_str, allow_negative=False):
    price = 1
    if allow_negative and price_str[0] == "(" and price_str[-1] == ")":
        price *= -1
        price_str = price_str[1:-1]
    if price_str[0] == "$":
        price_str = price_str[1:]
    dollars, cents = re.match(r"^([0-9,]+)\.([0-9]{2})$", price_str).groups()
    price *= int(cents) + 100 * int(dollars.replace(",", ""))
    return price

def dump_chase_txns(pdftext_input_json_filename, txns_output_json_filename,
                    discarded_text_output_filename):
    txns, discarded_text = parse_chase_pdftext(pdftext_input_json_filename)
    with open(txns_output_json_filename, "w") as fp:
        json.dump(txns, fp, sort_keys=True, indent=4)
    with open(discarded_text_output_filename, "w") as fp:
        fp.write(discarded_text)

def parse_chase_pdftext(json_filename):
    with open(json_filename) as fp:
        fragments = [TextFragment(*fragment) for fragment in json.load(fp)]
    it = PeekIterator(fragments, lookahead=1, lookbehind=2)
    discarded_text = io.StringIO()
    v1, v0 = fragments_discard_until(
        it, discarded_text,
        re.compile(r"(^Beginning Balance$)|(^Opening$)")).groups()
    if v1:
        assert not v0
        line = fragments_read_line(it)
        assert line[:-1] == ["Beginning Balance"], line
        opening_balance_str = line[-1]

        fragments_discard_until(it, discarded_text, "Ending Balance")
        line = fragments_read_line(it)
        assert line[:-1] == ["Ending Balance"], line
        closing_balance_str = line[-1]

        def discard_header(it):
            fragments_discard_until(it, discarded_text, "TRANSACTION DETAIL")
            it.__next__()

        discard_header(it)
        line = fragments_read_line(it)
        assert line == ["Beginning Balance", opening_balance_str], line

    elif v0:
        assert not v1
        line = fragments_read_line(it)
        assert line[:3] == ["Opening", "Balance", "$"], line
        opening_balance_str = line[3]

        line = fragments_read_line(it)
        assert line[:-1] == ["Additions", "$"]
        parse_price(line[-1])

        line = fragments_read_line(it)
        assert line[:-1] == ["Deductions", "$"]
        parse_price(line[-1])

        line = fragments_read_line(it)
        assert line[:-1] == ["Ending", "Balance", "$"], line
        closing_balance_str = line[-1]

        def discard_header(it):
            line = fragments_read_line(it)
            assert line == ["Activity"], line

            line = fragments_read_line(it)
            assert line == ["Date", "Description", "Additions",
                            "Deductions", "Balance"], line

        discard_header(it)
        line = fragments_read_line(it)
        assert len(line) == 5 and line[1:4] == ["Opening", "Balance", "$"]
        assert opening_balance_str == line[4]
        opening_date = line[0] # unused for now
    else:
        assert False

    txns = []
    while True:
        if it.peek(1).pageno != it.peek(0).pageno:
            # drop garbage from end of previous transaction
            if v1:
                assert re.match(r"Page +\d+ +of +\d+",
                                " ".join(txns[-1].info[-1]).strip()), \
                    txns[-1].info[-1]
                txns[-1].info.pop()

            fragments_discard_until(it, discarded_text, '(continued)')
            line = fragments_read_line(it)
            assert line == ["(continued)"], line
            discard_header(it)
            continue

        line = fragments_read_line(it)
        #print("  -- line -- {}".format(line))
        if v1:
            if line[0] == "Ending Balance":
                assert line[1:] == [closing_balance_str]
                break

        if not re.match(r"\d{2}/\d{2}", line[0]):
            txns[-1].info.append(line)
            continue

        if v0:
            if line[1:] == ['Ending', 'Balance', '$', closing_balance_str]:
                closing_date = line[0] # unused for now
                break

        txn = Txn()
        #print("newtxn {}".format(line))
        txn.info = [line]
        txns.append(txn)

    fragments_discard_until(it, discarded_text, None)

    opening_balance = parse_price(opening_balance_str)
    closing_balance = parse_price(closing_balance_str)
    cur_balance = opening_balance
    for txn in txns:
        #print("looptxn {}".format(txn.info))
        txn.old_balance = cur_balance
        words = txn.info[0 if v1 else -1]
        txn.new_balance = parse_price(words.pop())
        if v0: assert words.pop() == "$"
        txn.amount = parse_price(words.pop())
        if v0: assert words.pop() == "$"
        if v1:
          if words[-1] == "-":
            words.pop()
            txn.amount *= -1
        if v0:
          if txn.new_balance != txn.old_balance + txn.amount:
              txn.amount *= -1

        assert txn.new_balance == txn.old_balance + txn.amount, \
            (txn.new_balance, txn.old_balance, txn.amount)
        cur_balance = txn.new_balance

    assert cur_balance == closing_balance

    return [(txn.old_balance, txn.new_balance, txn.amount,
             [" ".join(line) for line in txn.info])
            for txn in txns], discarded_text.getvalue()


def fragments_discard_until(it, discarded_text, pattern):
    pattern_is_str = isinstance(pattern, str)
    while not it.at_end():
        fragment = it.peek(1)

        if pattern_is_str:
            if fragment.text == pattern: return
        elif pattern is not None:
            m = pattern.match(fragment.text)
            if m: return m

        if not it.at_start():
            prev_fragment = it.peek(0)
            discarded_text.write("\n" if prev_fragment.y != fragment.y else " ")
        discarded_text.write(fragment.text)
        it.__next__()

    if pattern is not None:
        raise Exception("unexpected end of file")

def fragments_read_line(it):
    words = []
    for fragment in it:
        words.append(fragment.text)
        if it.at_end() or it.peek(1).y != fragment.y:
            break
    return words


TextFragment = namedtuple("TextFragment", "pageno y x ord text")


class Txn:
    pass


class PeekIterator:
    """Iterator wrapper allowing peek at next and previous elements in sequence.

    Wrapper does not change underlying sequence at all, so for example:

        it = PeekIterator([2, 4, 6, 8, 10], lookahead=1, lookbehind=2):
        for x in it:
            print(x)

    will simply print the sequence 2, 4, 6, 8, 10.

    The main feature the iterator provides is a peek() method allowing
    access to preceding and following elements. So for example, when x
    is 6 it.peek(1) will return 8, it.peek(-1) will return(4), and
    it.peek(0) will return 6.
    """
    def __init__(self, it, lookahead=0, lookbehind=0):
        self.it = iter(it)
        self.lookahead = lookahead
        self.lookbehind = lookbehind
        self.cache = deque()
        self.prev_elems = 0  # cached elements previously returned by __next__.

        # Add next values from underlying iterator to lookahead cache.
        for _ in range(lookahead):
            try:
                self.cache.append(self.it.__next__())
            except StopIteration:
                break

    def __iter__(self):
        return self

    def __next__(self):
        # Add next value from underlying iterator to lookahead
        # cache. The if condition avoids a redundant call to
        # it.__next__() if a previous call raised StopIteration (in
        # which the lookahead section of the cache will have unused
        # capacity).
        if len(self.cache) >= self.lookahead + self.prev_elems:
            try:
                self.cache.append(self.it.__next__())
            except StopIteration:
                pass

        try:
            # If next sequence element is present in the cache, return
            # it and increment prev_elems. Otherwise the sequence is
            # over, so raise StopIteration.
            if self.prev_elems < len(self.cache):
                self.prev_elems += 1
                return self.cache[self.prev_elems - 1]
            else:
                assert self.prev_elems == len(self.cache)
                raise StopIteration

        finally:
            # Pop a value from the lookbehind cache if it is over
            # capacity from the append above.
            if self.prev_elems > self.lookbehind:
                self.cache.popleft()
                self.prev_elems -= 1
                assert self.prev_elems == self.lookbehind

    def peek(self, offset):
        """Return element of sequence relative to current iterator position.

        Value of "offset" argument controls which sequence element the
        peek call returns, according to chart below:

         Offset   Return value
          -2      ...
          -1      element that was returned by last last __next__() call
           0      element that was returned by last __next__() call
           1      element that will be returned by next __next__() call
           2      element that will be returned by next next __next__() call
           3      ...

        Call will fail if offset is not in range (-lookbind_size,
        lookahead_size] or if there is attempt to read values from
        after the end, or before the beginning of the sequence.
        """
        assert offset <= self.lookahead, \
            "PeekIterator lookahead value {} is too low to support peeks at " \
            "offset {}. Must increase lookahead to at least {}.".format(
                self.lookahead, self.offset, self.offset)
        assert offset > -self.lookbehind, \
            "PeekIterator lookbehind value {} is too low to support peeks at " \
            " offset {}. Must increase lookbehind to at least {}.".format(
                self.lookbehind, self.offset, 1 - self.offset)
        pos = self.prev_elems - 1 + offset
        assert pos >= 0, "Can't peek before first element in sequence."
        assert pos < len(self.cache), "Can't peek beyond last element in sequence."
        return self.cache[pos]

    def at_end(self):
        assert self.lookahead > 0, \
            "at_end method only available with lookahead > 0"
        return self.prev_elems >= len(self.cache)

    def at_start(self):
        assert self.lookbehind > 0, \
            "at_start method only available with lookbehind > 0"
        return self.prev_elems == 0
