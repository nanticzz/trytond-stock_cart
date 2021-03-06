# This file is part of stock_cart module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from time import sleep
from decimal import Decimal
from trytond.model import ModelView, ModelSQL, fields, Unique
from trytond.pool import Pool
from trytond.transaction import Transaction
from trytond.pyson import Eval, Equal, Not
from trytond.rpc import RPC
import logging

__all__ = ['StockCart', 'StockShipmentOutCart', 'StockShipmentOutCartLine']


logger = logging.getLogger(__name__)
STATES = {
    'readonly': Not(Equal(Eval('state'), 'draft')),
}


class StockCart(ModelSQL, ModelView):
    ' Stock Cart'
    __name__ = 'stock.cart'
    name = fields.Char('Name', required=True)
    rows = fields.Integer('Rows', required=True,
        help='Number of rows are available in this cart')
    columns = fields.Integer('Columns', required=True,
        help='Number of columns are available in this cart')
    total = fields.Function(fields.Integer('Total',
        help='Total boxes (rows * columns)'), 'on_change_with_total')
    active = fields.Boolean('Active')

    @staticmethod
    def default_active():
        return True

    @staticmethod
    def default_rows():
        return 1

    @staticmethod
    def default_columns():
        return 1

    @fields.depends('rows', 'columns')
    def on_change_with_total(self, name=None):
        if self.rows and self.columns:
            return self.rows * self.columns
        return 0


class StockShipmentOutCart(ModelSQL, ModelView):
    'Stock Shipment Out Cart'
    __name__ = 'stock.shipment.out.cart'
    _rec_name = 'shipment'
    shipment = fields.Many2One('stock.shipment.out', 'Shipment', required=True,
        states=STATES, depends=['state'], ondelete='CASCADE')
    cart = fields.Many2One('stock.cart', 'Cart', required=True,
        states=STATES, depends=['state'])
    user = fields.Many2One('res.user', 'User', required=True,
        states=STATES, depends=['state'])
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done'),
        ], 'State', readonly=True)

    @classmethod
    def __setup__(cls):
        super(StockShipmentOutCart, cls).__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('shipment_uniq', Unique(t, t.shipment),
                'The shipment must be unique!'),
            ]
        cls._order.insert(0, ('shipment', 'DESC'))
        cls._buttons.update({
            'done': {
                'invisible': Eval('state') == 'done',
                },
            'draft': {
                'invisible': Eval('state') == 'draft',
                },
            })
        cls.__rpc__.update({
            'get_products': RPC(readonly=False),
            'done_cart': RPC(readonly=False),
            })

    @staticmethod
    def default_state():
        return 'draft'

    @staticmethod
    def default_cart():
        User = Pool().get('res.user')
        user = User(Transaction().user)
        return user.cart.id if user.cart else None

    @staticmethod
    def default_user():
        return Transaction().user

    @classmethod
    @ModelView.button
    def done(cls, carts):
        cls.write(carts, {
            'state': 'done',
            })

    @classmethod
    @ModelView.button
    def draft(cls, carts):
        CartLine = Pool().get('stock.shipment.out.cart.line')

        cls.write(carts, {
            'state': 'draft',
            })

        domain = ['OR']
        for cart in carts:
            domain.append([
                ('shipment', '=', cart.shipment.id),
                ('cart', '=', cart.cart.id),
                ('user', '=', cart.user.id),
            ])
        lines_to_draft = CartLine.search(domain)
        if lines_to_draft:
            CartLine.draft(lines_to_draft)

    @classmethod
    def delete(cls, carts):
        CartLine = Pool().get('stock.shipment.out.cart.line')

        domain = ['OR']
        for cart in carts:
            domain.append([
                ('shipment', '=', cart.shipment.id),
                ('cart', '=', cart.cart.id),
                ('user', '=', cart.user.id),
            ])
        lines_to_delete = CartLine.search(domain)
        if lines_to_delete:
            CartLine.delete(lines_to_delete)
        super(StockShipmentOutCart, cls).delete(carts)

    @staticmethod
    def product_info(product):
        '''
        Return a dict with product info fields
        '''
        return {
            'name': product.name,
            'code': product.code,
            }

    @classmethod
    def get_products_by_carts(cls, carts):
        '''
        Return a list of dictionaries like this:
        [{
                product_id: {
                    'name': name_value,
                    'code': code_value,
                    'carts': [
                        cart_id,
                        ],
                    'shipments': [{
                            'id': shipment_id,
                            'code': code_value,
                            'quantity': quantity_value,
                            },
                        ]}},
            ]
        Where products are sorted by location path
        '''
        pool = Pool()
        Location = Pool().get('stock.location')
        User = pool.get('res.user')

        user = User(Transaction().user)

        if user.stock_locations:
            locations = list(user.stock_locations)
        elif user.stock_warehouse:
            locations = [user.stock_warehouse.storage_location]
        else:
            locations = []
            for warehouse in Location.search([
                    ('type', '=', 'warehouse'),
                    ]):
                locations.append(warehouse.storage_location)

        locs = Location.search([
                ('parent', 'child_of', [l.id for l in locations]),
                ])
        if locs:
            locations += locs
        location_ids = [l.id for l in locations]

        products = []
        for cart in carts:
            shipment = cart.shipment
            for move in shipment.inventory_moves:
                if move.state != 'assigned':
                    continue
                if move.from_location.id not in location_ids:
                    continue

                # If location has not sequence, put it in the end
                sequence = move.from_location.sequence or 1
                index = len(products)
                while index > 0 and products[index - 1][0] > sequence:
                    index -= 1

                jindex = index
                while (jindex > 0
                        # Check if different products have the same sequence
                        and move.product.id not in products[jindex - 1][1]):
                    jindex -= 1

                # location name will be used later to find the location ID
                location = move.from_location.name

                if jindex <= 0:
                    # Append this product to the list
                    product = cls.product_info(move.product)
                    product['shipments'] = [{
                            'id': shipment.id,
                            'code': shipment.number,
                            'quantity': move.quantity,
                            'location': location,
                            }]
                    product['carts'] = [cart.id]
                    product['quantity'] = move.quantity
                    locations = product.get('locations', [])
                    if not location in locations:
                        locations.append(location)
                    product['locations'] = locations
                    products.insert(index, (sequence,
                            {move.product.id: product}))
                else:
                    index = jindex
                    # Update current product because is already in the list
                    product = products[index - 1][1][move.product.id]
                    product['shipments'].append({
                                'id': shipment.id,
                                'code': shipment.number,
                                'quantity': move.quantity,
                                'location': location,
                                })
                    product['carts'].append(cart.id)
                    product['quantity'] += move.quantity
                    locations = product.get('locations', [])
                    if not location in locations:
                        locations.append(location)
                    product['locations'] = locations

        return [p[1] for p in products]

    @classmethod
    def append_domain(cls, domain):
        pass

    @classmethod
    def filter_domain_by_locations(cls, domain):
        pool = Pool()
        User = pool.get('res.user')
        Location = pool.get('stock.location')
        Move = pool.get('stock.move')

        user = User(Transaction().user)
        locations = user.stock_locations
        if locations:
            # search shipments are in user locations but not shipments
            # have other moves in others locations when user not have access
            # in locations preference
            locs = Location.search([
                    ('parent', 'child_of', [l.id for l in locations]),
                    ])
            locs_notin = Location.search([
                    ('id', 'not in', [l.id for l in locs]),
                    ])
            moves_in = Move.search([
                    ('state', '=', 'assigned'),
                    ('from_location', 'in', [l.id for l in locs]),
                    ('shipment', 'like', 'stock.shipment.out,%'),
                    ])
            moves_notin = Move.search([
                    ('state', '=', 'assigned'),
                    ('from_location', 'in', [l.id for l in locs_notin]),
                    ('shipment', 'like', 'stock.shipment.out,%'),
                    ])
            shipments_exclude = [m.shipment.id for m in moves_notin]
            moves = [m for m in moves_in if m.shipment.id not in shipments_exclude]
            domain.append(('moves', 'in', [m.id for m in moves]))

    @classmethod
    def filter_shipments(cls, shipments):
        return shipments

    @classmethod
    def get_products(cls, warehouse=None, state=['assigned'], attempts=0, total_attempts=5):
        '''
        Return a list shipments - RPC
        @param warehouse: ID warehouse domain to search shipments
        @param state: list. Shipment states to filter
        @param attempts: int. Attempts when table is lock
        @param total_attempts: int. Total attempts to try get shipments unlock
        '''
        pool = Pool()
        Shipment = pool.get('stock.shipment.out')
        Carts = pool.get('stock.shipment.out.cart')
        User = pool.get('res.user')

        transaction = Transaction()
        user = User(transaction.user)

        if not user.cart:
            logger.warning(
                'User %s not have cart in their preferences' % user.rec_name)
            return []
        baskets = user.cart.rows * user.cart.columns

        domain = [('state', 'in', state)]
        if warehouse:
            domain.append(('warehouse', '=', warehouse))
        cls.filter_domain_by_locations(domain)
        cls.append_domain(domain)
        try:
            # Locks transaction. Nobody can query this table
            transaction.database.lock(Transaction().connection, Carts._table)
        except:
            # Table is locked. Captures operational error and returns void list
            if attempts < total_attempts:
                sleep(0.5)
                return cls.get_products(warehouse, state, attempts + 1,
                    total_attempts)
            else:
                logger.warning(
                    'Table Carts is lock after %s attempts' % (total_attempts))
                return []
        else:
            # if there are carts state draft, return first this carts
            carts = Carts.search([
                ('state', '=', 'draft'),
                ('user', '=', user),
                ], limit=baskets)
            if carts:
                return cls.get_products_by_carts(carts)

            # Assign new shipments
            shipments = Shipment.search(domain,
                order=[('planned_date', 'ASC'), ('create_date', 'ASC')])
            shipments = cls.filter_shipments(shipments)

            pickings = [{'id': s.id, 'sequence': s.carrier.sequence or 999
                if hasattr(s, 'carrier') and s.carrier else 999} for s in shipments]
            shipments = [s['id'] for s in sorted(pickings, key=lambda k: k['sequence'])]

            carts_assigned = [c.shipment.id for c in Carts.search([
                ('shipment', 'in', shipments),
                ])]

            # Respect shipments order
            shipments_cart = [s for s in shipments if s not in carts_assigned]

            # Save carts assigned to user
            to_create = []
            for s in shipments_cart[:baskets]: # get limit from baskets
                to_create.append({'shipment': s})
            if to_create:
                carts = Carts.create(to_create)
                return cls.get_products_by_carts(carts)
        return []

    @classmethod
    def done_cart(cls, shipments):
        '''
        Done carts - RPC
        @param shipments: list codes
        '''
        pool = Pool()
        Carts = pool.get('stock.shipment.out.cart')
        ShipmentOut = pool.get('stock.shipment.out')

        shipments = ShipmentOut.search([
                ('code', 'in', shipments),
                ])

        if shipments:
            carts = Carts.search([
                ('state', '=', 'draft'),
                ('shipment', 'in', shipments),
                ])
            Carts.done(carts)

    @classmethod
    def print_shipments(cls, shipments):
        '''Custome print shipment method'''
        return


class StockShipmentOutCartLine(ModelSQL, ModelView):
    'Stock Shipment Out Cart Line'
    __name__ = 'stock.shipment.out.cart.line'
    _rec_name = 'shipment'
    shipment = fields.Many2One('stock.shipment.out', 'Shipment Out',
        required=True, states=STATES, depends=['state'], ondelete='CASCADE')
    from_location = fields.Many2One('stock.location', 'From Location',
        domain=[
            ('type', 'not in', ['warehouse', 'view']),
            ],
        required=True, states=STATES, depends=['state'])
    cart = fields.Many2One('stock.cart', 'Cart', required=True,
        states=STATES, depends=['state'])
    user = fields.Many2One('res.user', 'User', required=True,
        states=STATES, depends=['state'])
    product = fields.Many2One('product.product', 'Product',
        required=True, states=STATES, depends=['state'])
    product_uom_category = fields.Function(
        fields.Many2One('product.uom.category', 'Product Uom Category'),
        'on_change_with_product_uom_category')
    uom = fields.Many2One("product.uom", "Uom", required=True, states=STATES,
        domain=[
            ('category', '=', Eval('product_uom_category')),
            ],
        depends=['state', 'product_uom_category'])
    unit_digits = fields.Function(fields.Integer('Unit Digits'),
        'on_change_with_unit_digits')
    quantity = fields.Float("Quantity", required=True,
        digits=(16, Eval('unit_digits', 2)), states=STATES,
        depends=['state', 'unit_digits'])
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done'),
        ], 'State', readonly=True)

    @classmethod
    def __setup__(cls):
        super(StockShipmentOutCartLine, cls).__setup__()
        cls._buttons.update({
            'done': {
                'invisible': Eval('state') == 'done',
                },
            'draft': {
                'invisible': Eval('state') == 'draft',
                },
            })

    @staticmethod
    def default_cart():
        User = Pool().get('res.user')
        user = User(Transaction().user)
        return user.cart.id if user.cart else None

    @staticmethod
    def default_user():
        return Transaction().user

    @staticmethod
    def default_state():
        return 'done'

    @fields.depends('product')
    def on_change_with_product_uom_category(self, name=None):
        if self.product:
            return self.product.default_uom_category.id

    @fields.depends('uom')
    def on_change_with_unit_digits(self, name=None):
        if self.uom:
            return self.uom.digits
        return 2

    @fields.depends('product', 'uom',)
    def on_change_product(self):
        if self.product:
            self.uom = self.product.default_uom

    @classmethod
    @ModelView.button
    def done(cls, lines):
        cls.write(lines, {
            'state': 'done',
            })

    @classmethod
    @ModelView.button
    def draft(cls, lines):
        cls.write(lines, {
            'state': 'draft',
            })

    @classmethod
    def save_pickings(cls, pickings):
        'Save pickings lines'
        # pickings = {shipment: {product: qty}}
        pool = Pool()
        User = pool.get('res.user')
        ShipmentOut = pool.get('stock.shipment.out')
        Product = pool.get('product.product')
        Location = pool.get('stock.location')
        Configuration = pool.get('stock.configuration')

        user = User(Transaction().user)
        cart = user.cart if user.cart else None

        if not pickings or not cart:
            return

        config = Configuration(1)
        create_issue = config.stock_cart_create_issue or False

        domain = ['OR']
        shipments = []
        products = []
        locations = []
        for shipment_number, v in pickings.iteritems():
            if v['status'] == 'done':
                domain.append([
                    ('shipment.number', '=', shipment_number),
                    ('cart', '=', cart.id),
                    ('user', '=', user.id),
                ])
                shipments.append(shipment_number)
                products.append(int(v['product']))
                locations.append(v['location'])
            elif create_issue:
                cls.create_issue(shipment_number, v['status'],
                    v['product'], v['qty'], v['location'])

        if not shipments:
            return

        shipments = dict((s.number, s) for s in ShipmentOut.search([
                ('number', 'in', shipments),
                ]))
        products = dict((p.id, p) for p in Product.search([
                ('id', 'in', products),
                ]))
        locations = dict((s.name, s) for s in Location.search([
                ('name', 'in', locations),
                ]))

        picking_lines = []
        for line in cls.search(domain):
            # TODO all products has a product number
            picking_lines.append((line.shipment.number, line.product.id))

        to_create = []
        for shipment_number, v in pickings.iteritems():
            product_id = int(v['product'])
            qty = Decimal(v['qty'])

            if (shipment_number, product_id) in picking_lines:
                continue
            if not locations.get(v['location']):
                continue

            new_line = cls()
            new_line.shipment = shipments[shipment_number]
            new_line.from_location = locations[v['location']]
            new_line.product = products[product_id]
            new_line.quantity = qty
            new_line.on_change_product()
            to_create.append(new_line._save_values)

        if to_create:
            cls.create(to_create)
