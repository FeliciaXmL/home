import random
import bisect
import datetime
import io
import json
import os
import re
import sys
import time
import sqlite3
from collections import namedtuple, deque
from enum import IntEnum
from lxml import etree
from lxml.cssselect import CSSSelector


#
# Top-level functions called from shell.
#

def import_chase_txns(chase_dir, cash_db):
    with GnuCash(cash_db, "2016-02-27-pdfs") as gnu:
        acct_balance = None
        for filename in sorted(os.listdir(chase_dir)):
            if not filename.endswith(".json"):
                continue
            json_filename = os.path.join(chase_dir, filename)
            acct_balance = import_chase_statement(gnu, json_filename,
                                                  acct_balance)

        gnu.print_txns(lambda account, action, reconcile_state, **_:
                       account == gnu.checking_acct
                       and not action
                       and reconcile_state == 'y')


def dump_chase_txns(pdftext_input_json_filename, txns_output_json_filename,
                    discarded_text_output_filename):
    txns, discarded_text = parse_chase_pdftext(pdftext_input_json_filename)
    with open(txns_output_json_filename, "w") as fp:
        json.dump(txns, fp, sort_keys=True, indent=4)
    with open(discarded_text_output_filename, "w") as fp:
        fp.write(discarded_text)


def import_pay_txns(html_filename, cash_db):
    stubs = parse_mypay_html(html_filename)
    with GnuCash(cash_db, "2016-02-28-mypay") as gnu:
        accts = create_mypay_accts(gnu)
        import_mypay_stubs(gnu, accts, stubs)


#
# Chase gnucash import functions.
#

def import_chase_statement(gnu, json_filename, acct_balance):
    statement_date = parse_statement_date(json_filename)

    with open(json_filename) as fp:
        txns = json.load(fp)
        for date_str, prev_balance, balance, amount, desc in txns:
            date = (datetime.datetime.strptime(date_str, "%Y-%m-%d")
                    .date())
            action = date.strftime("%m/%d")
            memo = " || ".join(desc)
            if acct_balance is None:
                gnu.new_txn(statement_date, date, prev_balance,
                            gnu.opening_acct, gnu.checking_acct, action, "",
                            "Opening Balance")
            elif prev_balance != acct_balance:
                print (statement_date, date, prev_balance, acct_balance)
                gnu.new_txn(statement_date, date,
                            prev_balance - acct_balance,
                            gnu.imbalance_acct, gnu.checking_acct, action, "",
                            "Missing transactions")
            if amount < 0:
                gnu.new_txn(statement_date, date, amount,
                            gnu.expense_acct, gnu.checking_acct, action, memo,
                            "Withdrawal: {}".format(memo))
            else:
                gnu.new_txn(statement_date, date, amount,
                            gnu.income_acct, gnu.checking_acct, action, memo,
                            "Deposit: {}".format(memo))
            acct_balance = balance
    return acct_balance


#
# Chase pdf parsing functions.
#

def parse_chase_pdftext(json_filename):
    statement_date = parse_statement_date(json_filename)
    version = pdf_version(statement_date)
    newstyle = version >= Pdf.V2006_09

    # (date, description, additions, deductions, balance)
    if newstyle:
        columns = (700, 3500, None, 4500, 6000)
    else:
        columns = (700, 4100, 4800, 5400, 6000)

    with open(json_filename) as fp:
        fragments = [TextFragment(*fragment) for fragment in json.load(fp)]
    it = PeekIterator(fragments, lookahead=1, lookbehind=2)
    discarded_text = io.StringIO()
    # 2005-10-20 thru 2006-08-17
    #   - parses successfully
    # 2006-09-21
    #   - can't parse because transactions grouped by date
    #   - Switch to signed "AMOUNT" column instead of positive "Additions" and
    #     "Deductions" columns
    #   - Switch to "Beginning Balance" instead of "Opening Balance"
    #   - Fragments are now groups of words with spaces, instead of individual
    #     words
    #   - Transactions grouped by dates and only include daily balances, only
    #     way to distinguish them is by slightly larger vertical spacing
    #   - Dates no longer included on beginning/ending balance transaction lines
    #   - Has junk vertical barcode on side of statement that interferes with
    #     parsing
    #   - ALL CAPS transations with lots of extra wrapping, and multilevel
    #     indent
    # 2006-10-19 thru 2007-01-19
    #   - can't parse because transactions grouped by date
    #   - No more ALL CAPS transactions, no more multilevel indent
    # 2007-02-20 thru 2007-06-19
    #   - can't parse because deposit amounts are bolded, screw up read_line
    #   - Transactions no longer grouped by date
    # 2007-07-19
    #   - parses successfully
    #   - deposit bolding no longer moves stuff to new line and breaks parsing
    # 2007-08-17
    #   - can't parse because overdraft & negative balance
    # 2007-09-20 thru 2008-03-19
    #   - parses successfully
    # 2008-04-17 thru 2011-08-17
    #   - parses successfully
    #   - lines between transactions
    #   - slightly wider column
    # 2011-09-20 thru 2015-11-19
    #   - parses successfully
    #   - [" DATE", "DESCRIPTION", "AMOUNT", "BALANCE"] header is now image
    #     instead of text
    #   - Begin and ending header dates are no longer available
    found_begin, found_open = fragments_discard_until(
        it, discarded_text,
        re.compile(r"(^Beginning Balance$)|(^Opening$)")).groups()
    if newstyle:
        assert found_begin
        assert not found_open
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
            if it.peek(1).text == " DATE":
                line = fragments_read_line(it)
                assert line == [" DATE", "DESCRIPTION", "AMOUNT", "BALANCE"]

        discard_header(it)

        line = fragments_read_line(it)
        assert line == ["Beginning Balance", opening_balance_str], line
    else:
        assert found_open
        assert not found_begin
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

    txns = []
    while True:
        if it.peek(1).pageno != it.peek(0).pageno:
            # drop garbage from end of previous transaction
            fragments_discard_until(it, discarded_text, '(continued)')
            line = fragments_read_line(it)
            assert line == ["(continued)"], line
            discard_header(it)
            continue

        line_fragments = fragments_read_line_fragments(it)
        date, desc, add, ded, balance, junk = \
            fragments_split_columns(line_fragments, *columns)

        if 0:
            print("  -- line --")
            if date: print("     date {}".format(date))
            if desc: print("     desc {}".format(desc))
            if add: print("     add {}".format(add))
            if ded: print("     ded {}".format(ded))
            if balance: print("     balance {}".format(balance))
            if junk: print("     junk {}".format(junk))

        txn_date = None
        if date:
            assert len(date) == 1
            month, day = map(int, re.match(r"^(\d{2})/(\d{2})$",
                                           date[0].text).groups())
            txn_date = datetime.date(statement_date.year, month, day)
            if txn_date > statement_date:
                txn_date = txn_date.replace(year=statement_date.year - 1)
                assert txn_date <= statement_date
            assert txn_date >= statement_date - datetime.timedelta(35), txn_date

        # Detect non-transaction text
        # - Junk barcodes
        # - Page # of # footers
        # - Ending balance lines

        if junk:
            assert len(junk) == 1
            assert re.match("^[0-9]{20}$", junk[0].text)
            assert not date
            assert not desc
            assert not add
            assert not ded
            assert not balance
            continue

        if newstyle:
            if balance and balance[0].text == "Page":
                assert len(balance) == 4
                assert re.match(r" *\d+ *", balance[1].text)
                assert balance[2].text == "of"
                assert re.match(r" *\d+ *", balance[3].text)
                assert not date
                assert not desc
                assert not add
                assert not ded
                assert not junk
                continue

            if desc and desc[0].text == "Ending Balance":
                assert not date
                assert not add
                assert not ded
                assert not junk
                assert len(balance) == 1
                assert balance[0].text == closing_balance_str
                break
        else:
            if (len(desc) > 1 and desc[0].text == 'Ending'
                and desc[1].text == 'Balance'):
                assert len(desc) == 2
                assert txn_date == statement_date
                assert not add
                assert not ded
                assert not junk
                assert len(balance) == 2
                assert balance[0].text == "$"
                assert balance[1].text == closing_balance_str
                break

        # Parse transaction amount and balance

        txn_amount = None
        if add:
            assert not newstyle
            txn_amount = parse_price(add[-1].text)
            assert len(add) == 2
            assert add[0].text == "$"
        if ded:
            assert txn_amount is None
            txn_amount = parse_price(ded[-1].text)
            if newstyle:
                if len(ded) != 1:
                    assert len(ded) == 2
                    assert ded[0].text == "-"
                    txn_amount *= -1
            else:
                assert len(ded) == 2
                assert ded[0].text == "$"
                txn_amount *= -1

        txn_balance = None
        if balance:
            txn_balance = parse_price(balance[-1].text, allow_minus=True)
            if newstyle:
                assert len(balance) == 1
            else:
                assert len(balance) == 2
                assert balance[0].text == "$"

        # Determine whether line begins a new transaction. If line
        # contains a date it always indicates a new transaction. But
        # not every transaction has its own date (transactions are
        # grouped by date starting sep 2006, so also start a new
        # transaction when there is a tranaction amount. But avoid
        # doing this on oldstyle pdfs because they align amounts to
        # bottom of transaction instead of top.
        if txn_date is not None or (newstyle and txn_amount is not None):
            # If transaction is missing an amount value, look at
            # previous transaction to see if it consists solely of an
            # amount with no date, balance or description. If so,
            # remove that transaction and use the amount from it. This
            # is needed for a few statements starting feb 2007 which
            # add bolding to deposits amounts. The bolding moves up
            # the amount fragment text y positions slightly, isolating
            # them on their own lines.
            if newstyle and txn_amount is None:
                prev_txn = txns.pop()
                assert prev_txn.date is None
                assert prev_txn.amount is not None
                assert prev_txn.balance is None
                assert prev_txn.descs == [[]]
                txn_amount = prev_txn.amount
            txn = Txn()
            txn.date = txn_date
            txn.amount = txn_amount
            txn.balance = txn_balance
            txn.descs = [desc]
            txns.append(txn)
        else:
            assert txn_date is None
            if newstyle:
                assert txn_amount is None
                assert txn_balance is None
            else:
                if txn_amount is not None and txn.amount is None:
                    txn.amount = txn_amount
                if txn_balance is not None and txn.balance is None:
                    txn.balance = txn_balance
            assert desc
            txns[-1].descs.append(desc)

    fragments_discard_until(it, discarded_text, None)

    opening_balance = parse_price(opening_balance_str)
    closing_balance = parse_price(closing_balance_str)
    cur_balance = opening_balance
    txnit = PeekIterator(txns, lookahead=1, lookbehind=2)
    for txn in txnit:
        assert all(txn.descs)
        assert txn.amount is not None
        if txn.date is None:
            txn.date = txnit.peek(-1).date
        if txnit.prev_elems > 1:
            assert txnit.peek(-1).date <= txn.date
        if txn.balance is None:
            txn.balance = cur_balance + txn.amount
        else:
            assert txn.balance == cur_balance + txn.amount
        txn.prev_balance = cur_balance
        cur_balance = txn.balance
        if 0: print("txn {} {} {}\n    {}".format(
                txn.date, txn.amount, txn.balance, "\n    ".join(
                    "||".join(frag.text for frag in desc)
                    for desc in txn.descs)))
        continue

    assert cur_balance == closing_balance

    return [(txn.date.isoformat(), txn.prev_balance, txn.balance, txn.amount,
             [" ".join(frag.text for frag in desc)
              for desc in txn.descs])
            for txn in txns], discarded_text.getvalue()


def parse_statement_date(json_filename):
    return datetime.date(*[int(g) for g in re.match(
        r"^(?:.*?/)?([0-9]{4})-([0-9]{2})-([0-9]{2}).json$",
        json_filename).groups()])


def pdf_version(statement_date):
    vstr = "V{:%Y_%m}".format(statement_date)
    return Pdf(bisect.bisect(list(Pdf.__members__.keys()), vstr))


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


def fragments_read_line_fragments(it):
    words = []
    for fragment in it:
        words.append(fragment)
        if it.at_end() or it.peek(1).y != fragment.y:
            break
    return words


def fragments_read_line(it):
    return [fragment.text for fragment in fragments_read_line_fragments(it)]


def fragments_split_columns(fragments, *columns):
    ret = [[] for _ in range(len(columns)+1)]
    col_idx = 0
    for f in fragments:
        while (col_idx < len(columns)
               and (columns[col_idx] is None
                    or f.x > columns[col_idx])):
            col_idx += 1
        ret[col_idx].append(f)
    return ret


#
# Mypay gnucash import functions.
#

def create_mypay_accts(gnu):
    """Create mypay accounts, return mapping:

        (cat, title, desc) -> (account guid, goog_account_guid, flags)
    """
    splits = (
        (PAY, 'Group Term Life', "<p>Value of Life Insurance (as defined by "
         "IRS) for tax calculation. <div style=font-style:italic;>This has a "
         "matching deduction offset and is not paid out through Payroll.</div>"
         "</p><br><a href='https://sites.google.com/a/google.com/us-benefits/"
         "health-wellness/life-insurance-1' target='_blank'>Click here to "
         "learn more</a>",
         ("Income", "Taxable Benefits", "Google Life Insurance"), None, NONNEG),

        (PAY, 'Regular Pay', '<p>Regular wages for time worked at base salary '
         '/ hourly rate</p>',
         ("Income", "Salary", "Google Regular Pay"), None, NONNEG),

        (PAY, 'Vacation Pay', '<p>Vacation time taken against balance.</p>',
         ("Income", "Salary", "Google Vacation Pay"), None, NONNEG),

        # Taxable benefits folder tracks cost to google of non-cash
        # benefits that google pays for and i receive as goods or
        # services, and also owe taxes on
        #
        # Untaxed benefits folder tracks cost to google of non-cash
        # benefits that google pays for and i receive as good or
        # service, and do not owe any taxes on
        #
        # Actual benefits received are tracked in Expenses ->
        # Subsidized Hierarchy and show value of benefit that i
        # receive.
        #
        # Downside of this arrangement, is that neither account shows
        # money i personally pay for benefit. have to manually
        # subtract gym expense balance from gym benenfit balance to
        # see how much my decision to join gym actually costs me.
        #
        # An alternative to this arrangement would use one account
        # instead of two for each benefit, and balance would reflect
        # my actual cash expenditure. But then there would be no
        # account showing my tax liability, and also the gnucash ui
        # sucks when multiple lines in same account.
        (PAY, 'Gym Reim Txbl', '<p>Taxable Gym Reimbursement</p>',
         ("Income", "Taxable Benefits", "Google Gym Membership"), None, NONNEG),

        (PAY, 'Annual Bonus', "<p>Annual Bonus plan</p><br><a href='https://"
         "support.google.com/mygoogle/answer/4596076' target='_blank'>Click "
         "here to learn more</a>",
         ("Income", "Salary", "Google Annual Bonus"), None, NONNEG),

        (PAY, 'Prize/ Gift', '<p>Value of Prizes and Gifts for tax '
         'calculation. <div style=font-style:italic;>This has a matching '
         'deduction offset and is not paid out through Payroll.</div></p>',
         ("Income", "Taxable Benefits", "Google Holiday Gift"), None, NONNEG),

        (PAY, 'Prize/ Gift', '<p>Company-paid tax offset for Prizes and '
         'Gifts.</p>',
         ("Income", "Taxable Benefits", "Google Holiday Gift Tax Offset"),
         None, NONNEG),

        (PAY, 'Holiday Gift', '<p>Company-paid tax offset for Holiday '
         'Gift.</p>',
         ("Income", "Taxable Benefits", "Google Holiday Gift Tax Offset"),
         None, NONNEG),

        (PAY, 'Holiday Gift', '<p>Value of Holiday Gift for tax calculation. '
         '<div style=font-style:italic;>This has a matching deduction offset '
         'and is not paid out through Payroll.</div></p>',
         ("Income", "Taxable Benefits", "Google Holiday Gift"), None, NONNEG),

        (PAY, 'Peer Bonus', "<p>Peer Bonus payment. Thank you!</p><br><a "
         "href='https://support.google.com/mygoogle/answer/6003818?hl=en&"
         "ref_topic=3415454' target='_blank'>Click here to learn more</a>",
         ("Income", "Salary", "Google Peer Bonus"), None, NONNEG),

        (PAY, 'Patent Bonus', "<p>Patent Bonus payment</p><br><a href='https://"
         "sites.google.com/a/google.com/patents/patents/awards/monetary-"
         "awards' target='_blank'>Click here to learn more</a>",
         ("Income", "Salary", "Google Patent Bonus"), None, NONNEG),

        (PAY, 'Goog Stock Unit', "<p>Value of Google Stock Units (GSU) for tax "
         "calculation. <div style=font-style:italic;>This is not paid out "
         "through Payroll.</div></p><br><a href='https://sites.google.com/a/"
         "google.com/stock-admin-landing-page/' target='_blank'>Click here to "
         "learn more</a>",
         ("Income", "Taxable Benefits", "Google Stock Units"), None, NONNEG),

        (PAY, 'Retroactive Pay', '<p>Adjustment to wages from a previous pay '
         'period.</p>',
         ("Income", "Salary", "Google Wage Adjustment"), None, 0),

        (PAY, 'Refund Report', '<p>Non-pay-impacting code for metadata '
         'tracking</p>',
         ("Income", "Salary", "Google Wage Adjustment"), None, 0),

        (PAY, 'Placeholder', '<p>Non-pay-impacting code for metadata '
         'tracking</p>',
         ("Income", "Salary", "Google Wage Adjustment"), None, 0),

        (PAY, 'Spot Bonus', "<p>Spot Bonus payment</p><br><a href='https://"
         "support.google.com/mygoogle/answer/6003815?hl=en&ref_topic=3415454' "
         "target='_blank'>Click here to learn more</a>",
         ("Income", "Salary", "Google Spot Bonus"), None, NONNEG),

        (PAY, 'Vacation Payout', '<p>Liquidation of Vacation balance.</p>',
         ("Income", "Salary", "Google Vacation Payout"), None, NONNEG),

        (DED, 'Group Term Life', '<p>Offset deduction; see matching earning '
         'code for more info</p>',
         ("Expenses", "Subsidized", "Google Life Insurance"), None, NONNEG),

        (DED, '401K Pretax', '<p>Pre-tax 401k contribution defined as a '
         'percentage or dollar election of eligible earnings</p>\n<p><a href='
         '"https://support-content-draft.corp.google.com/mygoogle/topic/'
         '6205846?hl=en&ref_topic=6206133" target="_blank">Click here to learn '
         'more</a></p>',
         ("Assets", "Investments", "401K"),
         ("Income", "Untaxed Benefits", "Google Employer 401K Contribution"),
         NONNEG),

        (DED, 'Medical', "<p>Employee contribution towards Medical Insurance "
         "plan</p><br><a href='https://sites.google.com/a/google.com/us-"
         "benefits/health-wellness/medical-benefits' target='_blank'>Click "
         "here to learn more</a>",
         ("Expenses", "Subsidized", "Google Medical Insurance"), None, NONNEG),

        (DED, 'Gym Deduction', "<p>Employee contribution towards Gym "
         "Membership</p><br><a href='https://sites.google.com/a/google.com/"
         "us-benefits/health-wellness/gyms-and-fitness-g-fit' target='_blank'>"
         "Click here to learn more</a>",
         ("Expenses", "Subsidized", "Google Gym Membership"), None, NONNEG),

        (DED, 'Pretax 401 Flat', '<p>Pre-tax 401k contribution defined as a '
         'dollar amount per pay cycle</p>\n<p><a href="https://support-content-'
         'draft.corp.google.com/mygoogle/topic/6205846?hl=en&ref_topic='
         '6206133" target="_blank">Click here to learn more</a></p>',
         ("Assets", "Investments", "401K"),
         ("Income", "Untaxed Benefits", "Google Employer 401K Contribution"),
         NONNEG),

        (DED, 'Dental', "<p>Employee contribution towards Dental Insurance "
         "premiums</p><br><a href='https://sites.google.com/a/google.com/"
         "us-benefits/health-wellness/dental-insurance' target='_blank'>Click "
         "here to learn more</a>",
         ("Expenses", "Subsidized", "Google Dental Insurance"), None, NONNEG),

        (DED, 'Vision', "<p>Employee contribution towards Vision Insurance "
         "premiums</p><br><a href='https://sites.google.com/a/google.com/us-"
         "benefits/health-wellness/vision-insurance' target='_blank'>Click "
         "here to learn more</a>",
         ("Expenses", "Subsidized", "Google Vision Insurance"), None, NONNEG),

        (DED, 'Prize Gross Up', '<p>Offset deduction; see matching earning '
        'code for more info</p>',
         ("Expenses", "Subsidized", "Google Holiday Gift"), None, NONNEG),

        (DED, 'Holiday Gift', '<p>Offset deduction; see matching earning code '
        'for more info</p>',
         ("Expenses", "Subsidized", "Google Holiday Gift"), None, NONNEG),

        (DED, 'RSU Stock Offst', '<p>Offset deduction; see matching earning '
        'code for more info</p>',
         ("Assets", "Investments", "Google Stock Units"), None, NONNEG),

        (DED, 'GSU Refund', '<p>Refund for overage of stock withholding.<bt >'
        '</bt>When your stock vests, Google is required to recognize its value '
        'for taxes and executes enough units of stock to cover these taxes. '
        'Since Google can only execute stock in whole units, there is often a '
        'remainder amount from the sale which is returned to you through this '
        'deduction code.</p>',
         ("Assets", "Investments", "Google Stock Units"), None, NONPOS),

        # https://www.irs.gov/Affordable-Care-Act/Form-W-2-Reporting-of-Employer-Sponsored-Health-Coverage
        # W2 Box 12, Code DD
        (DED, 'ER Benefit Cost', '<p>Total contributions by Employer towards '
         'benefits for W-2 reporting</p>',
         ("Expenses", "Subsidized", "Google Medical Insurance"),
         ("Income", "Untaxed Benefits", "Google Employer Medical Insurance "
          "Contribution"), NONNEG | GOOG_ONLY),

        # FIXME: notmuch search for 14.95 charge on 9/2014 to see what
        # this was, then add expense transaction to 0 out liability
        # balance
        (DED, 'GCard Repayment', '<p>Collection for personal charges on a '
         'GCard</p>',
         ("Liabilities", "Google GCard"), None, NONNEG),

        (TAX, 'Employee Medicare', '',
         ("Expenses", "Taxes", "Medicare"), None, NONNEG),

        (TAX, 'Federal Income Tax', '',
         ("Expenses", "Taxes", "Federal"), None, NONNEG),

        (TAX, 'New York R', '<p>New York R City Tax</p>',
         ("Expenses", "Taxes", "NYC Resident"), None, NONNEG),

        (TAX, 'NY Disability Employee', '<p>New York Disability</p>',
         ("Expenses", "Taxes", "NY State Disability Insurance (SDI)"), None,
         NONNEG),

        (TAX, 'NY State Income Tax', '<p>New York State Income Tax</p>',
         ("Expenses", "Taxes", "NY State Income"), None, NONNEG),

        (TAX, 'Social Security Employee Tax', '',
         ("Expenses", "Taxes", "Social Security"), None, NONNEG),
    )

    def acct_type(names):
        # Verify account names begin with Google, with exceptions for
        # taxes and 401k.
        assert (names[-1].startswith("Google")
                or names[:2] == ("Expenses", "Taxes")
                or names == ("Assets", "Investments", "401K"))

        # Do the actual work.
        if names[0] == "Expenses":
            return "EXPENSE"
        if names[0] == "Income":
            return "INCOME"
        if names[0] == "Assets":
            return "BANK"
        if names[0] == "Liabilities":
            return "CREDIT"
        assert False, names

    # Create placeholder accts
    gnu.acct(("Expenses", "Subsidized"), acct_type="EXPENSE")
    gnu.acct(("Income", "Untaxed Benefits"), acct_type="INCOME")

    accts = {}
    for cat, title, desc, acct, goog_acct, flags in splits:
        assert (cat, title, desc) not in accts
        acct = gnu.acct(acct, acct_type=acct_type(acct))
        if goog_acct:
          goog_acct = gnu.acct(goog_acct, acct_type=acct_type(goog_acct))
        accts[(cat, title, desc)] = acct, goog_acct, flags

    return accts


def import_mypay_stubs(gnu, accts, stubs):
    for paydate_str, docid, netpay, splits in stubs:
        paydate = datetime.datetime.strptime(paydate_str, "%m/%d/%Y").date()
        description = "Google Document {} Net Pay ${:,}.{:02}".format(
            docid, netpay // 100, netpay % 100)

        closest_offset = None
        if netpay:
            c = gnu.conn.cursor()
            c.execute("SELECT s.guid, t.guid, t.post_date, t.description "
                      "FROM splits AS s "
                      "INNER JOIN transactions AS t ON (t.guid = s.tx_guid) "
                      "WHERE s.account_guid = ? AND s.value_num = ?",
                      (gnu.checking_acct, netpay))
            for sguid, tguid, post_date_str, desc in c.fetchall():
                post_date = gnu.date(post_date_str)
                offset = abs((post_date - paydate).days)
                if (closest_offset is None or offset < closest_offset):
                    closest_offset = offset
                    closest_split = sguid
                    closest_txn = tguid
                    closest_desc = desc

        if closest_offset is None:
            assert not netpay
            txn_guid = gnu.guid()
            gnu.insert("transactions",
                        (("guid", txn_guid),
                        ("currency_guid", gnu.commodity_usd),
                        ("num", ""),
                        ("post_date", gnu.date_str(paydate)),
                        ("enter_date", gnu.date_str(paydate)),
                        ("description", description)))
        else:
            assert (closest_desc in ("Google 0", "Google 1",
                                     "Google Bonus", "Google")
                    or closest_desc.startswith("Deposit: Google Inc       "
                                               "Payroll                    "
                                               "PPD ID: "))
            assert closest_offset < 7
            txn_guid = closest_txn
            c.execute("UPDATE transactions SET post_date = ?, enter_date = ?, "
                      "    description = ? WHERE guid = ?",
                      (gnu.date_str(paydate), gnu.date_str(paydate),
                       description, txn_guid))
            assert c.rowcount == 1

            delete_splits = []
            c.execute("SELECT guid, tx_guid, account_guid, memo, action, "
                      "    reconcile_state, reconcile_date, value_num, "
                      "    value_denom, quantity_num, quantity_denom, lot_guid "
                      "FROM splits WHERE tx_guid = ?", (txn_guid,))
            for (guid, tx_guid, account_guid, memo, action, reconcile_state,
                 reconcile_date, value_num, value_denom, quantity_num,
                 quantity_denom, lot_guid) in c.fetchall():
                if guid == closest_split:
                    continue
                assert action == ""
                assert memo in ("", 'Federal Income Tax', 'Employee Medicare',
                                'Social Security E', 'NY State Income T',
                                'New York R', 'NY Disability Emp', 'Dental',
                                'Medical', 'Pretax 401 Flat', 'Vision',
                                'Group Term Life', 'Regular', 'Gym Deduction',
                                'Taxable Gym Reim', 'Annual Bonus',
                                'Med Dent Vis', 'Vacation Pay', '401K Pretax',
                                'Social Security', 'NY State Income'), memo
                assert reconcile_state == "n"
                assert reconcile_date is None
                assert lot_guid is None
                delete_splits.append(guid)

            c.execute("DELETE FROM splits WHERE guid IN ({})".format(
                ",".join("?" for _ in delete_splits)), (delete_splits))
            assert c.rowcount == len(delete_splits)

        for cat, (title, desc), details, amount, goog_amount in splits:
            acct, goog_acct, flags = accts[cat, title, desc]
            assert flags & NONNEG == 0 or amount >= 0
            assert flags & NONPOS == 0 or amount <= 0
            assert flags & GOOG_ONLY == 0 or amount == 0
            assert flags & NONNEG == 0 or goog_amount >= 0
            assert flags & NONPOS == 0 or goog_amount <= 0
            assert goog_acct or goog_amount == 0
            assert amount or goog_amount

            if cat == PAY:
                amount *= -1

            if amount:
                memo = "{}: {}{}".format(cat, title, details)
                gnu.new_split(txn_guid, acct, amount, memo)

            if goog_amount:
                assert cat == DED
                memo = "Employer contribution: {}{}".format(title, details)
                gnu.new_split(txn_guid, goog_acct, -goog_amount, memo)
                memo = "Employer deduction: {}{}".format(title, details)
                gnu.new_split(txn_guid, acct, goog_amount, memo)


#
# Mypay html parsing functions.
#

def print_mypay_html(html_filename):
    for paydate_str, docid, netpay, splits in parse_mypay_html(html_filename):
        print(paydate_str, docid, netpay)
        for cat, label, details, amount in splits:
            print("  {}: {}{} -- {}".format(cat, label, details, amount))


def parse_mypay_html(filename):
    pay = etree.parse(filename, etree.HTMLParser())
    stubs = []
    for check in CSSSelector('.payStatement')(pay):
        assert len(check) == 1
        tbody = check[0]
        assert tbody.tag == "tbody"

        if len(tbody) == 10:
            # delete extra company logo column in newer html file
            assert len(tbody[1]) == 1 # logo
            assert tbody[1][0].attrib["colspan"] == "5"
            assert tbody[1][0][0].tag == "img"
            del tbody[1:2]

        assert len(tbody) == 9
        assert len(tbody[0]) == 5 # blank columns

        assert len(tbody[1]) == 1 # logo
        assert tbody[1][0].attrib["colspan"] == "5"
        assert tbody[1][0][-1].attrib["id"] == "companyLogo"

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

        assert len(tbody[5]) == 2
        assert tbody[5][0].attrib["colspan"] == "2"
        assert tbody[5][0].attrib["rowspan"] == "2"
        assert tbody[5][0][0][0].text == "Earnings"
        total = 0
        splits = []
        for label, details, current, goog_current in tab(tbody[5][0], docid, PAY):
            current = parse_price(current, True)
            goog_current = parse_price(goog_current, True)
            total += current
            splits.append((PAY, label, details, current, goog_current))

        assert tbody[5][1].attrib["colspan"] == "3"
        assert tbody[5][1][0][0].text == "Deductions"
        for label, details, current, goog_current in tab(tbody[5][1], docid, DED):
            current = parse_price(current, True)
            goog_current = parse_price(goog_current, True)
            total -= current
            splits.append((DED, label, details, current, goog_current))

        assert len(tbody[6]) == 1
        assert tbody[6][0].attrib["colspan"] == "3"
        assert tbody[6][0][0][0].text == "Taxes"
        for label, details, current, goog_current in tab(tbody[6][0], docid, TAX):
            current = parse_price(current, True)
            goog_current = parse_price(goog_current, True)
            total -= current
            splits.append((TAX, label, details, current, goog_current))

        stubs.append((paydate, docid, netpay, splits))

        assert total == netpay, (total, netpay)
        assert len(tbody[8]) == 1
        assert tbody[8][0][0][0].text == "Pay summary"

    return stubs

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
                title = bodycol[0].attrib["data-title"]
                title2 = bodycol[0].text.strip()
                assert title == title2
                desc = bodycol[0].attrib["data-content"]
                bodycols.append((title, desc))
            else:
                assert len(bodycol) == 0
                bodycols.append(bodycol.text)
        details = ""
        goog_current = "$0.00"
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
            assert garbage == "\xa0"
        else:
            assert table_type == TAX
            label, income, current, ytd, garbage = bodycols
            income_val = parse_price(income)
            current_val = parse_price(current)
            if income_val == 0:
                details += " (based on {})".format(income)
            else:
                details += " ({:.3f}% × {})".format(current_val / income_val * 100.0, income)
            assert garbage == "\xa0"
        if current != "$0.00" or goog_current != "$0.00":
          yield label, details, current, goog_current


#
# General utility functions.
#

def parse_price(price_str, allow_negative=False, allow_minus=False):
    price = 1
    if allow_negative and price_str[0] == "(" and price_str[-1] == ")":
        price *= -1
        price_str = price_str[1:-1]
    if allow_minus and price_str[0] == "-":
        price *= -1
        price_str = price_str[1:]
    if price_str[0] == "$":
        price_str = price_str[1:]
    dollars, cents = re.match(r"^([0-9,]+)\.([0-9]{2})$", price_str).groups()
    price *= int(cents) + 100 * int(dollars.replace(",", ""))
    return price


def s(elem):
    return etree.tostring(elem, pretty_print=True).decode()


#
# Enums and constants.
#

# Mypay account flags.
NONNEG = 1
NONPOS = 2
GOOG_ONLY = 4

# Mypay split types.
PAY = "Pay"
DED = "Deduction"
TAX = "Tax"

# Pdf versions.
Pdf = IntEnum("Pdf", "V2005_10 V2006_09 V2006_10 V2007_02 V2007_07 V2007_08 "
              "V2007_09 V2008_04 V2011_09")


#
# Class types.
#

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
        assert pos < len(self.cache), "Can't peek after last sequence element."
        return self.cache[pos]

    def at_end(self):
        assert self.lookahead > 0, \
            "at_end method only available with lookahead > 0"
        return self.prev_elems >= len(self.cache)

    def at_start(self):
        assert self.lookbehind > 0, \
            "at_start method only available with lookbehind > 0"
        return self.prev_elems == 0


class GnuCash:
    def __init__(self, db_file, seed):
        self.db_file = db_file
        self.random = random.Random()
        self.random.seed(seed)

    def __enter__(self):
        self.conn = sqlite3.connect(self.db_file)
        self.conn.isolation_level = None
        self.conn.cursor().execute("BEGIN")
        self.commodity_usd = self.currency("USD")
        self.opening_acct = self.acct(("Equity", "Opening Balances"))
        self.imbalance_acct = self.acct(("Imbalance-USD",))
        self.expense_acct = self.acct(("Expenses",))
        self.income_acct = self.acct(("Income",))
        self.checking_acct = self.acct(( "Assets", "Current Assets", "Checking Account"))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if 1: self.conn.cursor().execute("COMMIT")
        self.conn.close()

    def insert(self, table, vals):
        c = self.conn.cursor()
        s = "INSERT INTO {} ({}) VALUES ({})".format(
            table,
            ",".join(k for k, v in vals),
            ",".join("?" for k, v, in vals))
        c.execute(s, tuple(v for k, v in vals))

    def guid(self):
        return '%032x' % self.random.randrange(16**32)

    def acct(self, names, acct_type=None, create_parents=False):
        c = self.conn.cursor()
        c.execute("SELECT guid FROM accounts "
                  "WHERE name = ? AND parent_guid IS NULL",
                  ("Root Account",))
        rows = c.fetchall()
        assert len(rows) == 1
        guid, = rows[0]

        it = PeekIterator(names, lookahead=1)
        for name in it:
            c.execute("SELECT guid FROM accounts "
                      "WHERE name = ? AND parent_guid = ?", (name, guid))
            rows = c.fetchall()
            if len(rows) == 1:
                guid, = rows[0]
                continue

            assert len(rows) == 0
            assert acct_type is not None
            assert it.at_end() or create_parents, names
            guid = self.new_acct(guid, name, acct_type)

        return guid

    def currency(self, mnemonic):
        c = self.conn.cursor()
        c.execute("SELECT guid FROM commodities WHERE mnemonic = ?",
                  (mnemonic,))
        rows = c.fetchall()
        assert len(rows) == 1
        guid, = rows[0]
        return guid

    def new_acct(self, parent_guid, name, acct_type="ASSET"):
        guid = self.guid()
        self.insert("accounts",
                    (("guid", guid),
                     ("name", name),
                     ("account_type", acct_type),
                     ("commodity_guid", self.commodity_usd),
                     ("commodity_scu", 100),
                     ("non_std_scu", 0),
                     ("parent_guid", parent_guid),
                     ("code", ""),
                     ("description", ""),
                     ("hidden", 0),
                     ("placeholder", 0)))
        return guid

    def date(self, date_str):
        # date_str comment below explains this madness.
        return datetime.datetime.fromtimestamp(
            datetime.datetime.strptime(date_str, "%Y%m%d%H%M%S")
            .replace(tzinfo=datetime.timezone.utc).timestamp()).date()

    def date_str(self, date):
        # Have to add local time zone offset to string, because
        # gnucash idiotically extracts date from the string after
        # doing unnecessary utc->local timestamp conversion which
        # subtracts 4 or 5 hours and results in yesterday's date being
        # shown in the UI.
        return datetime.datetime.utcfromtimestamp(
            time.mktime(date.timetuple())).strftime("%Y%m%d%H%M%S")

    def new_txn(self, reconcile_date, date, amount, src_acct, dst_acct,
                action, memo, description):
        if action and memo:
            c = self.conn.cursor()
            c.execute("SELECT s.guid, t.guid, t.post_date, s.memo FROM splits AS s "
                      "INNER JOIN transactions AS t ON (t.guid = s.tx_guid) "
                      "WHERE s.account_guid = ? AND s.value_num = ? AND s.action = ''",
                      (dst_acct, amount))

            closest_offset = closest_split = None
            for split_guid, txn_guid, txn_date_str, split_memo in c.fetchall():
                assert not split_memo or split_memo in ('Checking',), split_memo
                txn_date = self.date(txn_date_str)
                offset = abs((txn_date - date).days)
                if offset < 10 and (closest_offset is None or offset < closest_offset):
                    closest_offset = offset
                    closest_split = split_guid

            if closest_split is not None:
                c = self.conn.cursor()
                c.execute("UPDATE splits SET memo=?, action=?, reconcile_state=?, "
                          "reconcile_date=? WHERE guid = ?",
                          (memo, action, "y" if reconcile_date else "n",
                          self.date_str(reconcile_date), closest_split))
                assert c.rowcount == 1
                return

        txn_guid = self.guid()

        self.insert("transactions",
                    (("guid", txn_guid),
                     ("currency_guid", self.commodity_usd),
                     ("num", ""),
                     ("post_date", self.date_str(date)),
                     ("enter_date", self.date_str(date)),
                     ("description", description)))

        # Could merge this boilerplate with new_split code below.
        src_split = (("guid", self.guid()),
                     ("tx_guid", txn_guid),
                     ("account_guid", src_acct),
                     ("memo", ""),
                     ("action", ""),
                     ("reconcile_state", "n"),
                     ("reconcile_date", None),
                     ("value_num", -amount),
                     ("value_denom", 100),
                     ("quantity_num", -amount),
                     ("quantity_denom", 100),
                     ("lot_guid", None))

        dst_split = (("guid", self.guid()),
                     ("tx_guid", txn_guid),
                     ("account_guid", dst_acct),
                     ("memo", memo),
                     ("action", action),
                     ("reconcile_state", "y" if reconcile_date else "n"),
                     ("reconcile_date", self.date_str(reconcile_date)
                      if reconcile_date else None),
                     ("value_num", amount),
                     ("value_denom", 100),
                     ("quantity_num", amount),
                     ("quantity_denom", 100),
                     ("lot_guid", None))

        if amount < 0:
            src_split, dst_split = dst_split, src_split
        self.insert("splits", src_split)
        self.insert("splits", dst_split)

    def new_split(self, txn_guid, acct, amount, memo):
        guid = self.guid()
        self.insert("splits",
                    (("guid", guid),
                      ("tx_guid", txn_guid),
                      ("account_guid", acct),
                      ("memo", memo),
                      ("action", ""),
                      ("reconcile_state", "n"),
                      ("reconcile_date", None),
                      ("value_num", amount),
                      ("value_denom", 100),
                      ("quantity_num", amount),
                      ("quantity_denom", 100),
                      ("lot_guid", None)))
        return guid

    def print_txns(self, split_filter):
        acct_map = {}
        c = self.conn.cursor()
        c.execute("SELECT guid, name FROM accounts")
        for guid, name in c.fetchall():
            acct_map[guid] = name

        c = self.conn.cursor()
        c.execute("SELECT guid, post_date, description FROM transactions "
                  "ORDER BY post_date, rowid")
        for guid, post_date, description in c.fetchall():
            d = self.conn.cursor()
            d.execute("SELECT account_guid, memo, action, value_num, "
                      "reconcile_state "
                      "FROM splits WHERE tx_guid = ? ORDER BY rowid", (guid,))

            found_split = False
            splits = []
            for account, memo, action, value, reconcile_state in d.fetchall():
                splits.append((account, memo, action, value))
                if split_filter(account=account, memo=memo, action=action,
                                value=value, reconcile_state=reconcile_state):
                    found_split = True

            if found_split:
                print(self.date(post_date), description)
                for account, memo, action, value in splits:
                  print(" {:9.2f}".format(value/100.0), acct_map[account], memo)


