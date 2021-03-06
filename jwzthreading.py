"""jwzthreading.py

Contains an implementation of an algorithm for threading mail
messages, as described at http://www.jwz.org/doc/threading.html.

To use:

  Create a bunch of Message instances, one per message to be threaded,
  filling in the .subject, .message_id, and .references attributes.
  You can use the .message attribute to record the RFC-822 message object,
  or some other piece of information for your own purposes.

  Call the thread() function with a list of the Message instances.

  You'll get back a {subject line -> Container} dictionary; each
  container may have a .children attribute giving descendants of each
  message.  You'll probably want to sort these children by date, subject,
  or some other criterion.

Copyright (c) 2003-2010, A.M. Kuchling.

This code is under a BSD-style license; see the LICENSE file for details.
"""

from __future__ import print_function
from collections import deque
import re

__all__ = ['Message', 'thread']


#
# constants
#

MSGID_RE = re.compile(r'<([^>]+)>')
SUBJECT_RE = re.compile(
    r'((Re(\[\d+\])?:) | (\[ [^]]+ \])\s*)+', re.I | re.VERBOSE)


#
# models
#

class Container(object):
    """Contains a tree of messages.

    Attributes:
        message (Message): Message corresponding to this tree node.
            This can be None, if a Message-Id is referenced but no
            message with the ID is included.
        children ([Container]): Possibly-empty list of child containers
        parent (Container): Parent container, if any
    """
    def __init__(self):
        self.message = self.parent = None
        self.children = []

    def __repr__(self):
        return '<%s %x: %r>' % (self.__class__.__name__, id(self),
                                self.message)

    def is_dummy(self):
        """Check if Container has a message."""
        return self.message is None

    def add_child(self, child):
        """Add a child to `self`.

        Arguments:
            child (Container): Child to add.
        """
        if child.parent:
            child.parent.remove_child(child)
        self.children.append(child)
        child.parent = self

    def remove_child(self, child):
        """Remove a child from `self`.

        Arguments:
            child (Container): Child to remove.
        """
        self.children.remove(child)
        child.parent = None

    def has_descendant(self, ctr):
        """Check if `ctr` is a descendant of this.

        Arguments:
            ctr (Container): possible descendant container.

        Returns:
            True if `ctr` is a descendant of `self`, else False.
        """
        # To avoid recursing indefinitely, we'll do a depth-first
        # search; 'seen' tracks the containers we've already seen,
        # and 'stack' is a deque containing containers that we need
        # to look at.
        stack = deque()
        stack.append(self)
        seen = set()

        while stack:
            node = stack.pop()
            if node is ctr:
                return True
            seen.add(node)
            for child in node.children:
                if child not in seen:
                    stack.append(child)

        return False


class Message(object):
    """Represents a message to be threaded.

    Attributes:
        subject (str): Subject line of the message.
        message_id (str): Message ID as retrieved from the Message-ID
            header.
        references ([str]): List of message IDs from the In-Reply-To
            and References headers.
        message (any): Can contain information for the caller's use
            (e.g. an RFC-822 message object).
    """
    message = None
    message_id = None
    references = []
    subject = None

    def __init__(self, msg=None):
        if msg is None:
            return

        msg_id = MSGID_RE.search(msg.get('Message-ID', ''))
        if msg_id is None:
            raise ValueError('Message does not contain a Message-ID: header')

        self.message = msg
        self.message_id = msg_id.group(1)

        self.references = uniq(MSGID_RE.findall(msg.get('References', '')))
        self.subject = msg.get('Subject', "No subject")

        # Get In-Reply-To: header and add it to references
        msg_id = MSGID_RE.search(msg.get('In-Reply-To', ''))
        if msg_id:
            msg_id = msg_id.group(1)
            if msg_id not in self.references:
                self.references.append(msg_id)

    def __repr__(self):
        return '<%s: %r>' % (self.__class__.__name__, self.message_id)


#
# functions
#

def uniq(alist):
    result = {}
    return [result.setdefault(e, e) for e in alist if e not in result]


def prune_container(container):
    """Prune a tree of containers.

    Recursively prune a tree of containers, as described in step 4 of
    the algorithm. Returns a list of the children that should replace
    this container.

    Arguments:
        container (Container): Container to prune

    Returns:
        List of zero or more containers.
    """
    # Prune children, assembling a new list of children
    new_children = []

    for ctr in container.children[:]:  # copy the container.children list
        pruned_child = prune_container(ctr)
        new_children.extend(pruned_child)
        container.remove_child(ctr)

    for child in new_children:
        container.add_child(child)

    if container.message is None and not len(container.children):
        # step 4 (a) - nuke empty containers
        return []
    elif container.message is None and (
        len(container.children) == 1 or container.parent is not None):
        # step 4 (b) - promote children
        children = container.children[:]
        for child in children:
            container.remove_child(child)
        return children
    else:
        # Leave this node in place
        return [container]


def thread(messages):
    """Thread a list of mail items.

    Takes a list of Message objects, and returns a dictionary mapping
    subjects to Containers. Containers are trees, with the `children`
    attribute containing a list of subtrees, so callers can then sort
    children by date or poster or whatever.

    Arguments:
        messages ([Message]): List of Message itesms

    Returns:
        dict of containers, with subject as the key
    """
    # step one
    id_table = {}

    for msg in messages:
        # step one (a)
        this_container = id_table.get(msg.message_id, None)
        if this_container is not None:
            this_container.message = msg
        else:
            this_container = Container()
            this_container.message = msg
            id_table[msg.message_id] = this_container

        # step one (b)
        prev = None
        for ref in msg.references:
            container = id_table.get(ref, None)
            if container is None:
                container = Container()
                id_table[ref] = container

            if prev is not None:
                # Don't add link if it would create a loop
                if container is this_container:
                    continue
                if container.has_descendant(prev):
                    continue
                prev.add_child(container)

            prev = container

        if prev is not None:
            prev.add_child(this_container)

    # step two - find root set
    root_set = [container for container in id_table.values()
                if container.parent is None]

    # step three - delete id_table
    del id_table

    # step four - prune empty containers
    for container in root_set:
        assert container.parent == None

    new_root_set = []
    for container in root_set:
        new_container = prune_container(container)
        new_root_set.extend(new_container)

    root_set = new_root_set

    # step five - group root set by subject
    subject_table = {}
    for container in root_set:
        if container.message:
            subj = container.message.subject
        else:
            subj = container.children[0].message.subject

        subj = SUBJECT_RE.sub('', subj)
        if subj == '':
            continue

        existing = subject_table.get(subj, None)
        if (existing is None or
            (existing.message is not None and
             container.message is None) or
            (existing.message is not None and
             container.message is not None and
             len(existing.message.subject) > len(container.message.subject))):
            subject_table[subj] = container

    # step five (c)
    for container in root_set:
        if container.message:
            subj = container.message.subject
        else:
            subj = container.children[0].message.subject

        subj = SUBJECT_RE.sub('', subj)
        ctr = subject_table.get(subj)

        if ctr is None or ctr is container:
            continue

        if ctr.is_dummy() and container.is_dummy():
            for child in ctr.children:
                container.add_child(child)
        elif ctr.is_dummy() or container.is_dummy():
            if ctr.is_dummy():
                ctr.add_child(container)
            else:
                container.add_child(ctr)
        elif len(ctr.message.subject) < len(container.message.subject):
            # ctr has fewer levels of 're:' headers
            ctr.add_child(container)
        elif len(ctr.message.subject) > len(container.message.subject):
            # container has fewer levels of 're:' headers
            container.add_child(ctr)
        else:
            new = Container()
            new.add_child(ctr)
            new.add_child(container)
            subject_table[subj] = new

    return subject_table


def print_container(ctr, depth=0, debug=0):
    """Print summary of Thread to stdout."""
    if debug:
        message = repr(ctr)
    else:
        message = str(ctr.message and ctr.message.subject)

    print(''.join(['> ' * depth, message]))

    for child in ctr.children:
        print_container(child, depth + 1)


def main():
    import mailbox
    import sys

    msglist = []

    print('Reading input file...')
    mbox = mailbox.mbox(sys.argv[1])
    for message in mbox:
        try:
            parsed_msg = Message(message)
        except ValueError:
            continue
        msglist.append(parsed_msg)

    print('Threading...')
    subject_table = thread(msglist)

    print('Output...')
    subjects = subject_table.items()
    subjects.sort()
    for _, container in subjects:
        print_container(container)

if __name__ == "__main__":
    main()
