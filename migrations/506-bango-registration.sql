BEGIN;
CREATE TABLE `payments_seller` (
    `id` int(11) UNSIGNED AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `created` datetime NOT NULL,
    `modified` datetime NOT NULL,
    `user_id` int(11) UNSIGNED NOT NULL,
    `uuid` varchar(255) NOT NULL,
    `resource_uri` varchar(255) NOT NULL
) ENGINE=InnoDB CHARACTER SET utf8 COLLATE utf8_general_ci
;
ALTER TABLE `payments_seller` ADD CONSTRAINT `user_id_refs_id_29692a2a` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`);
CREATE INDEX `payments_seller_fbfc09f1` ON `payments_seller` (`user_id`);


CREATE TABLE `bango_account` (
    `id` int(11) UNSIGNED AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `created` datetime NOT NULL,
    `modified` datetime NOT NULL,
    `user_id` int(11) UNSIGNED NOT NULL,
    `package_uri` varchar(255) NOT NULL,
    `name` varchar(64) NOT NULL,
    `inactive` bool NOT NULL,
    UNIQUE (`user_id`, `package_uri`)
) ENGINE=InnoDB CHARACTER SET utf8 COLLATE utf8_general_ci
;
ALTER TABLE `bango_account` ADD CONSTRAINT `user_id_refs_id_c5b6b654` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`);
CREATE INDEX `bango_account_fbfc09f1` ON `bango_account` (`user_id`);


CREATE TABLE `addon_bango` (
    `id` int(11) UNSIGNED AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `created` datetime NOT NULL,
    `modified` datetime NOT NULL,
    `addon_id` int(11) UNSIGNED NOT NULL UNIQUE,
    `bango_account_id` int(11) UNSIGNED NOT NULL,
    UNIQUE (`addon_id`, `bango_account_id`)
) ENGINE=InnoDB CHARACTER SET utf8 COLLATE utf8_general_ci
;
ALTER TABLE `addon_bango` ADD CONSTRAINT `addon_id_refs_id_5070d6dd` FOREIGN KEY (`addon_id`) REFERENCES `addons` (`id`);
ALTER TABLE `addon_bango` ADD CONSTRAINT `bango_account_id_refs_id_86851482` FOREIGN KEY (`bango_account_id`) REFERENCES `bango_account` (`id`);
CREATE INDEX `addon_bango_cd9b1428` ON `addon_bango` (`bango_account_id`);

COMMIT;
