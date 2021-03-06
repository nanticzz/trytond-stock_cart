# This file is part of stock_cart module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.transaction import Transaction

__all__ = ['Inventory', 'InventoryLine']


class Inventory:
    __metaclass__ = PoolMeta
    __name__ = 'stock.inventory'

    @classmethod
    def confirm(cls, inventories):
        # Confirm an inventory do a write and write call again complete_lines
        # We add confirm in the context to not skip to calculate get_picking_quantity
        with Transaction().set_context(confirm_inventory=True):
            super(Inventory, cls).confirm(inventories)

    @classmethod
    def complete_lines(cls, inventories, fill=True):
        # can't call Line.create_values4complete() because we don't have the product.
        # At the moment, to add new values is call complete_lines (yes, other write)
        super(Inventory, cls).complete_lines(inventories, fill)

        if Transaction().context.get('confirm_inventory', False):
            return

        if fill:
            cls.complete_lines(inventories, fill=False)


class InventoryLine:
    __metaclass__ = PoolMeta
    __name__ = 'stock.inventory.line'
    picking_quantity = fields.Float('Picking Quantity',
        digits=(16, Eval('unit_digits', 2)), depends=['unit_digits'])

    @staticmethod
    def default_picking_quantity():
        return 0

    @classmethod
    def get_picking_quantity(cls, location, products):
        """"
        Return a dict with product ID and picking quantity
        """
        CartLine = Pool().get('stock.shipment.out.cart.line')

        vals = {}
        for line in CartLine.search([
                ('product', 'in', products),
                ('from_location', '=', location),
                ('shipment.state', '=', 'assigned')
                ]):
            product_id = line.product.id
            if product_id in vals:
                vals[product_id] += line.quantity
            else:
                vals[product_id] = line.quantity
        return vals

    @fields.depends('product', 'inventory')
    def on_change_product(self):
        super(InventoryLine, self).on_change_product()

        if self.product:
            vals = self.get_picking_quantity(self.inventory.location, [self.product])
            self.picking_quantity = vals.get(self.product.id, 0)

    def get_move(self):
        self.quantity += self.picking_quantity
        move = super(InventoryLine, self).get_move()
        return move

    def update_values4complete(self, quantity):
        '''
        Return update values to complete inventory
        '''
        values = super(InventoryLine, self).update_values4complete(quantity)
        picking_quantity = self.get_picking_quantity(self.inventory.location,
            [self.product]).get(self.product.id, 0)
        if (self.expected_quantity == self.quantity == quantity and
                self.picking_quantity != picking_quantity):
            values['picking_quantity'] = picking_quantity
            values['quantity'] = max(quantity - picking_quantity, 0.0)
        return values
